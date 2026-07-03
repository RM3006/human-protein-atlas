{% macro bronze_view_sql(view_name, path) %}
CREATE OR REPLACE VIEW bronze.{{ view_name }} AS
SELECT * FROM read_parquet('{{ path }}')
{% endmacro %}
