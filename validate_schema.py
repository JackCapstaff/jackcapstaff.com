#!/usr/bin/env python
"""
Schema validation script - compares model definitions with actual database schema
"""

from app import app, db


def get_model_columns():
    """Extract column definitions from models that are already loaded in app"""
    with app.app_context():
        schema = {}
        
        # Get all model classes from the app
        model_classes = {
            'User': app.User,
            'NewsItem': app.NewsItem,
            'Event': app.Event,
            'PageContent': app.PageContent,
            'ContactMessage': app.ContactMessage,
        }
        
        for model_name, model_class in model_classes.items():
            columns = {}
            for column in model_class.__table__.columns:
                col_info = {
                    'type': str(column.type),
                    'nullable': column.nullable,
                    'primary_key': column.primary_key,
                }
                columns[column.name] = col_info
            schema[model_name] = columns
        
        return schema


def get_db_schema():
    """Get actual schema from database"""
    with app.app_context():
        connection = db.engine.connect()
        dialect_name = db.engine.dialect.name
        
        schema = {}
        
        try:
            if dialect_name == 'postgresql':
                # PostgreSQL
                query = """
                    SELECT 
                        t.tablename,
                        c.attname as column_name,
                        t.typname as data_type,
                        c.attnotnull as not_null
                    FROM 
                        pg_tables t
                        JOIN pg_class pc ON pc.relname = t.tablename
                        JOIN pg_attribute c ON c.attrelid = pc.oid
                        JOIN pg_type t ON t.oid = c.atttypid
                    WHERE 
                        t.schemaname = 'public'
                        AND t.tablename IN ('user', 'news_item', 'event', 'page_content', 'contact_message')
                        AND c.attnum > 0
                    ORDER BY t.tablename, c.attnum;
                """
                result = connection.execute(db.text(query)).fetchall()
                
                for row in result:
                    table, col, dtype, not_null = row
                    if table not in schema:
                        schema[table] = {}
                    schema[table][col] = {
                        'type': dtype,
                        'nullable': not not_null
                    }
                    
            elif dialect_name == 'sqlite':
                # SQLite
                table_names = ['user', 'news_item', 'event', 'page_content', 'contact_message']
                for table_name in table_names:
                    query = f"PRAGMA table_info({table_name});"
                    try:
                        result = connection.execute(db.text(query)).fetchall()
                        schema[table_name] = {}
                        for row in result:
                            cid, name, dtype, not_null, dflt_value, pk = row
                            schema[table_name][name] = {
                                'type': dtype,
                                'nullable': not bool(not_null)
                            }
                    except:
                        pass
        finally:
            connection.close()
        
        return schema


def compare_schemas(model_schema, db_schema):
    """Compare model schema with database schema"""
    print("=" * 80)
    print("DATABASE SCHEMA VALIDATION")
    print("=" * 80)
    
    issues = []
    
    for model_name in sorted(model_schema.keys()):
        model_cols = model_schema[model_name]
        db_table_name = model_name[0].lower() + ''.join(['_' + c.lower() if c.isupper() else c for c in model_name[1:]])
        
        print(f"\n📋 {model_name}")
        print(f"   Table: {db_table_name}")
        
        if db_table_name not in db_schema:
            print(f"   ⚠️  TABLE NOT FOUND IN DATABASE")
            issues.append(f"Missing table: {db_table_name}")
            continue
        
        db_cols = db_schema[db_table_name]
        
        # Check for missing columns
        missing = set(model_cols.keys()) - set(db_cols.keys())
        if missing:
            print(f"   ❌ MISSING COLUMNS: {', '.join(sorted(missing))}")
            for col in sorted(missing):
                issues.append(f"{db_table_name}.{col}")
        else:
            print(f"   ✓ All {len(model_cols)} columns present")
        
        # Check for extra columns in DB
        extra = set(db_cols.keys()) - set(model_cols.keys())
        if extra:
            print(f"   ⚠️  Extra columns in DB (not in model): {', '.join(sorted(extra))}")
        
        # Show column details
        print(f"\n   Columns:")
        for col_name in sorted(model_cols.keys()):
            model_type = model_cols[col_name]['type']
            nullable = model_cols[col_name]['nullable']
            db_type = db_cols.get(col_name, {}).get('type', 'MISSING')
            
            status = "✓" if col_name in db_cols else "❌"
            nullable_str = "NULL" if nullable else "NOT NULL"
            print(f"      {status} {col_name:30} {model_type:20} {nullable_str}")
    
    print("\n" + "=" * 80)
    if issues:
        print(f"\n⚠️  ISSUES FOUND ({len(issues)}):")
        for issue in issues:
            print(f"   - {issue}")
        print("\n💡 Before deploying, you need to:")
        print("   1. Run: heroku run python migrate_add_read_column.py")
        print("   2. Or redeploy which will auto-run the migration")
    else:
        print("\n✅ ALL SCHEMAS UP TO DATE - SAFE TO DEPLOY!")
    
    print("=" * 80)
    
    return len(issues) == 0


if __name__ == '__main__':
    with app.app_context():
        model_schema = get_model_columns()
        db_schema = get_db_schema()
        success = compare_schemas(model_schema, db_schema)
        exit(0 if success else 1)
