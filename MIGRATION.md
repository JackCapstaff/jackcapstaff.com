# Database Migration Instructions

## Issue
The Heroku database is missing the `read` column on the `contact_message` table, causing a 500 error:
```
ProgrammingError: column "contact_message.read" does not exist
```

## Solution

### Option 1: Deploy the fix (Recommended)
The migration is now integrated into your deployment process:

1. **Commit and push the changes:**
   ```bash
   git add migrate_add_read_column.py Procfile
   git commit -m "Add database migration for contact_message.read column"
   git push origin main
   ```

2. **Deploy to Heroku:**
   ```bash
   git push heroku main
   ```

   Heroku will automatically run the migration as part of the release phase before starting the web server.

### Option 2: Run migration on existing Heroku app (Immediate fix)
If you need to fix the production database immediately without redeploying:

```bash
heroku run python migrate_add_read_column.py -a rehearsal-schedule-5e89cff7249e
```

Replace `rehearsal-schedule-5e89cff7249e` with your actual Heroku app name.

### Option 3: Manual SQL (if needed)
If the above doesn't work, connect to your Heroku PostgreSQL database directly:

```bash
heroku pg:psql -a rehearsal-schedule-5e89cff7249e
```

Then run:
```sql
ALTER TABLE contact_message ADD COLUMN read BOOLEAN DEFAULT FALSE NOT NULL;
```

## Migration Details

The migration script:
- ✓ Checks if the column already exists (idempotent)
- ✓ Works with both SQLite (dev) and PostgreSQL (Heroku production)
- ✓ Has appropriate defaults for existing rows
- ✓ Handles errors gracefully

## Files Changed
- `migrate_add_read_column.py` - New migration script
- `Procfile` - Added release phase to auto-run migrations on deploy
