release: python migrate_add_read_column.py && FLASK_CONFIG=production flask db upgrade
web: gunicorn app:app
