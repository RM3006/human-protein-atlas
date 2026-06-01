"""Modal App: ESM-2 t33_650M batch inference on A10G GPU.

Single entry-point: embed_batch, which takes a list of amino-acid sequences
and returns a paired list of (1280-dim float32 embedding, was_truncated) tuples.
Sequences longer than MAX_SEQ_LEN residues are truncated before tokenization.
The model is baked into the image at build time to avoid cold-start downloads.
"""

from __future__ import annotations

import modal

ESM_MODEL = "facebook/esm2_t33_650M_UR50D"
# ESM-2 context window is 1024 tokens; <cls> and <eos> consume 2 slots.
MAX_SEQ_LEN = 1022


def _download_model() -> None:
    from transformers import EsmModel, EsmTokenizer  # type: ignore[import-untyped]

    EsmTokenizer.from_pretrained(ESM_MODEL)
    EsmModel.from_pretrained(ESM_MODEL)


image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch>=2.4", "transformers>=4.40", "numpy>=1.26")
    .run_function(_download_model)
)

app = modal.App("atlas-esm2", image=image)


def truncate_sequence(seq: str, max_len: int = MAX_SEQ_LEN) -> tuple[str, bool]:
    """Clip seq to max_len residues; second element flags whether clipping occurred."""
    if len(seq) > max_len:
        return seq[:max_len], True
    return seq, False


@app.function(  # pyright: ignore[reportUnknownMemberType]
    gpu="A10G",
    timeout=3600,
    max_containers=5,
)
def embed_batch(sequences: list[str]) -> list[tuple[list[float], bool]]:
    """Mean-pool ESM-2 last hidden state for a batch of amino-acid sequences.

    Produces: one (1280-dim embedding, was_truncated) tuple per input sequence.
    Depends on: ESM-2 t33_650M baked into the Modal image.
    All computation runs in fp16 to fit batches of 256 on a single A10G (24 GB).
    """
    import torch  # type: ignore[import-untyped]
    from transformers import EsmModel, EsmTokenizer  # type: ignore[import-untyped]

    tokenizer = EsmTokenizer.from_pretrained(ESM_MODEL)
    model = EsmModel.from_pretrained(ESM_MODEL, torch_dtype=torch.float16)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = model.to(device)
    model.eval()

    clipped_seqs, flags = zip(*(truncate_sequence(s) for s in sequences), strict=True)

    inputs = tokenizer(
        list(clipped_seqs),
        return_tensors="pt",
        padding=True,
        truncation=False,  # already truncated above; avoids silent double-truncation
        max_length=MAX_SEQ_LEN + 2,
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)

    # Mean-pool over all non-padding tokens (includes <cls> and <eos>).
    hidden: torch.Tensor = outputs.last_hidden_state  # (B, L, 1280) fp16
    mask: torch.Tensor = inputs["attention_mask"].unsqueeze(-1).float()  # (B, L, 1)
    summed = (hidden.float() * mask).sum(dim=1)  # (B, 1280)
    counts = mask.sum(dim=1).clamp(min=1.0)  # (B, 1)
    embeddings = (summed / counts).cpu().numpy()  # (B, 1280) float32

    return [
        (row.tolist(), bool(was_truncated))
        for row, was_truncated in zip(embeddings, flags, strict=True)
    ]
