# Brassing Around - Event & Story Management System

A Flask-based web application for Brassing Around volunteers to manage events and share stories with photos. Features role-based access with regular users who can view and comment, and admin users who can create and manage content.

## Features

- 🔐 **Two-Tier User System**: 
  - Regular users can view events and comment on stories
  - Admin users can create/edit events and stories (invitation-only)
- 🎫 **Admin Invitation System**: Admins can generate time-limited invitation codes
- 📅 **Event Management**: Create, edit, and delete events (admins only)
- 📝 **Story Creation**: Add timestamped stories with titles to events (admins only)
- 💬 **Comments**: All registered users can comment on stories
- 📸 **Photo Uploads**: Upload multiple photos to stories (admins only)
- 👁️ **Public View**: Public-facing pages to view events and stories
- 🎨 **Bootstrap UI**: Clean, responsive design

## Installation

### Prerequisites
- Python 3.7 or higher
- pip (Python package installer)

### Setup Instructions

1. **Install Dependencies**
   ```powershell
   pip install -r requirements.txt
   ```

2. **Initialize the Database**
   The database will be automatically created when you first run the application.

3. **Run the Application**
   ```powershell
   python app.py
   ```

4. **Access the Application**
   - Open your browser and navigate to: `http://localhost:5000`
   - The application will be running on port 5000

## First-Time Setup

### 1. Create Initial Admin Account

Run the admin creation script:
```powershell
python create_admin.py
```

Follow the prompts to create your first admin user. This account will have full privileges to manage events and create invitation codes.

### 2. Login and Generate Invitation Codes

- Log in with your admin account at `http://localhost:5000/login`
- Go to the Admin Dashboard
- In the "Admin Invitations" section, generate invitation codes
- Valid periods: 1, 7, 14, or 30 days
- Share these codes with volunteers you want to grant admin access

### 3. Regular User Registration

Anyone cRegular Users

**Viewing Events:**
- Browse all events on the homepage
- Click "View Event" to see stories and photos
- Click on photos to view them full-size

**Commenting:**
- Log in to your account
- Navigate to any event with stories
- Scroll to the comments section under each story
- Type your comment and click "Post Comment"
- Delete your own comments using the "Delete" button

**Requesting Admin Access:**
- Contact an existing admin volunteer
- Ask them to generate an invitation code for you
- Register a new account with the invitation code, or ask an admin to upgrade your existing account

### For Admin Users

**Managing Admin Invitations:**
- View active invitation codes in the Admin Dashboard
- Generate new codes with different validity periods (1-30 days)
- Copy codes to clipboard to share with new admins
- Revoke unused codes if needed

**Managing Events:**
- View all events from the Admin Dashboard
- Create new events with title, description, date, and location
- Edit events using the "Edit" button
- Delete events (this also deletes all associated stories, comments, and photos)

**Managing Stories:**
- Click on an event to see its stories
- Add new stories with the "Add Story" button
- Upload multiple photos per story
- Edit or delete stories as needed
- Delete individual photos from stories

**Photo Management:**
- Supported formats: PNG, JPG, JPEG, GIF
- Maximum file size: 25MB per photo
- Photos are automatically timestamped when uploaded

**Moderating Comments:**
- Admins can delete any user's comments
- Helps maintain community guidelines
**Managing Stories:**
- Click on an event to see its stories
- Add new stories with the "Add Story" button
- Upload multiple photos per story
- Edit or delete stories as needed
- Delete individual photos from stories
 with comments
│   ├── login.html             # Login page
│   ├── register.html          # Registration page (with invitation code field)
- Maximum file size: 25MB per photo
- Photos are automatically timestamped when uploaded

### For Public Viewers

- Visit the homepage to see all events
- Click "View Event" to see stories and photos
- Click on photos to view them full-size

## Project Structure

```
Brassing Around/
│
├── app.py                      # Main Flask application
├── requirements.txt            # Python dependencies
├── brassing_around.db         # SQLite database (created automatically)
│
├── templates/    all user accounts
- username, email, password_hash, is_admin (Boolean), created_at

**Events**: Stores event information
- title, description, event_date, location, created_by, created_at

**Stories**: Stores stories linked to events
- event_id, title, content, timestamp, created_by

**Photos**: Stores photo information linked to stories
- story_id, filename, caption, uploaded_at

**Comments**: Stores user comments on stories
- story_id, user_id, content, created_at

**AdminInvitations**: Stores invitation codes for admin access
- code, created_by, used_by, created_at, expires_at, is_used
│       ├── create_story.html
│       └── edit_story.html
│
├── static/
│   └── uploads/               # Uploaded photos stored here
│
├─No Admin Users Exist:**
- Run `python create_admin.py` to create the initial admin account
- This script can be run multiple times if needed

**Can't Access Admin Features:**
- Verify your account has admin privileges (check navbar for "Admin" label)
- If not an admin, obtain an invitation code from an existing admin
- Register a new account with the code, or ask an admin for help

**Invitation Code Issues:**
- Codes expire after their validity period
- Each code can only be used once
- Admins can revoke unused codes from the dashboard

**Database Issues:**
- Delete `brassing_around.db` and restart the app to recreate the database
- Run `python create_admin.py` again to create a new admin account

**Upload Errors:**
- Ensure the `static/uploads` directory exists and is writable
- Check file size doesn't exceed 25MB

**Login Issues:**
- Clear browser cookies and try again
- Verify username/password are correct

**Comment Not Posting:**
- Ensure you're logged in
- Comments cannot be empty
**Events**: Stores event information
- title, description, event_date, location, created_by, created_at

**Stories**: Stores stories linked to events
- event_id, title, content, timestamp, created_by

**Photos**: Stores photo information linked to stories
- story_id, filename, caption, uploaded_at
 and comments
- Event categories and tags
- Search functionality
- Email invitations instead of manual code sharing
- User profile pages
- React/like buttons for stories
- Export events to PDF
- Calendar view of events
- Photo galleries by event
- Comment threading (replies to comments)
4. Set `debug=False` in `app.run()`
5. Use environment variables for sensitive configuration

## Troubleshooting

**Database Issues:**
- Delete `brassing_around.db` and restart the app to recreate the database

**Heroku Data Loss On Redeploy:**
- Heroku dyno files are ephemeral, so SQLite will be reset on deploy/restart.
- Production must use Heroku Postgres via `DATABASE_URL`.
- Set `PUBLIC_BASE_URL` to your live domain for Facebook story previews.
- If `DATABASE_URL` is missing on Heroku, the app now fails fast instead of silently using throwaway SQLite.

**Upload Errors:**
- Ensure the `static/uploads` directory exists and is writable
- Check file size doesn't exceed 25MB

**Login Issues:**
- Clear browser cookies and try again
- Verify username/password are correct

## Future Enhancements

- Email notifications for new events
- Event categories and tags
- Search functionality
- User roles (volunteers vs. viewers)
- Export events to PDF
- Calendar view of events
- Comments on stories

## Support

For issues or questions, contact your Brassing Around administrator.

---

Built with ❤️ for Brassing Around volunteers
