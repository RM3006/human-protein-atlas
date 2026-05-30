"""Project logger.

Production paths use ``from atlas.logging import logger`` instead of ``print``
(CLAUDE.md rule 8). A single module-level logger keeps formatting consistent
across assets, resources, and scripts.
"""

import logging
import os

logger = logging.getLogger("atlas")

if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(os.environ.get("ATLAS_LOG_LEVEL", "INFO"))
    logger.propagate = False
