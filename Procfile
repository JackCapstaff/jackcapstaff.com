release: python migrate_add_read_column.py && cd quiz_app && flask db upgrade || true
web: gunicorn app:app
