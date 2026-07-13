release: python migrate_add_read_column.py && cd quiz_app && FLASK_APP=wsgi:app FLASK_CONFIG=production flask db upgrade || true
web: gunicorn app:app
