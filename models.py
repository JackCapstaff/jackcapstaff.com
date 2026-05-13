"""
Database models for Jack Capstaff CMS
"""
from datetime import datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


def init_models(db):
    """Initialize database models with SQLAlchemy instance"""
    
    class User(UserMixin, db.Model):
        """User model with role-based permissions"""
        id = db.Column(db.Integer, primary_key=True)
        username = db.Column(db.String(120), unique=True, nullable=False, index=True)
        email = db.Column(db.String(255), unique=True, nullable=False, index=True)
        password_hash = db.Column(db.String(255), nullable=False)
        name = db.Column(db.String(255))
        
        # Role-based access: 'admin', 'editor', 'viewer'
        role = db.Column(db.String(50), default='viewer', nullable=False)
        is_active = db.Column(db.Boolean, default=True, nullable=False)
        
        # Password reset flow
        reset_token = db.Column(db.String(255), unique=True, nullable=True, index=True)
        reset_token_expiry = db.Column(db.DateTime, nullable=True)
        
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
        
        # Relationships
        news_items = db.relationship('NewsItem', backref='author', lazy=True, cascade='all, delete-orphan')
        events = db.relationship('Event', backref='author', lazy=True, cascade='all, delete-orphan')
        page_content = db.relationship('PageContent', backref='author', lazy=True, cascade='all, delete-orphan')

        def set_password(self, password):
            self.password_hash = generate_password_hash(password)

        def check_password(self, password):
            return check_password_hash(self.password_hash, password)
        
        def generate_reset_token(self):
            """Generate a unique reset token and set expiry to 1 hour from now"""
            import secrets
            self.reset_token = secrets.token_urlsafe(32)
            self.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)
            return self.reset_token
        
        def verify_reset_token(self, token):
            """Check if token is valid and not expired"""
            if not self.reset_token or self.reset_token != token:
                return False
            if not self.reset_token_expiry:
                return False
            if datetime.utcnow() > self.reset_token_expiry:
                return False
            return True
        
        def clear_reset_token(self):
            """Clear reset token after use"""
            self.reset_token = None
            self.reset_token_expiry = None
        
        def has_permission(self, permission):
            """Check if user has specific permission based on role"""
            permissions = {
                'admin': ['view_news', 'edit_news', 'create_news', 'delete_news',
                         'view_events', 'edit_events', 'create_events', 'delete_events',
                         'view_pages', 'edit_pages', 'create_pages', 'delete_pages',
                         'manage_users', 'view_admin'],
                'editor': ['view_news', 'edit_news', 'create_news', 'delete_news',
                          'view_events', 'edit_events', 'create_events', 'delete_events',
                          'view_pages', 'edit_pages', 'create_pages', 'delete_pages'],
                'viewer': ['view_news', 'view_events', 'view_pages'],
            }
            role_perms = permissions.get(self.role, [])
            return permission in role_perms
        
        def is_admin(self):
            return self.role == 'admin'
        
        def is_editor(self):
            return self.role in ('admin', 'editor')


    class NewsItem(db.Model):
        """News article model"""
        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(255), nullable=False)
        slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
        subtitle = db.Column(db.String(255))
        content = db.Column(db.Text, nullable=False)
        excerpt = db.Column(db.Text)
        featured_image = db.Column(db.String(512))
        published = db.Column(db.Boolean, default=False)
        published_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
        
        author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

        __table_args__ = (
            db.Index('ix_published_date', 'published', 'published_at'),
        )


    class Event(db.Model):
        """Event/concert model"""
        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(255), nullable=False)
        slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
        description = db.Column(db.Text)
        event_date = db.Column(db.DateTime, nullable=False, index=True)
        location = db.Column(db.String(255))
        
        # External links
        tickets_url = db.Column(db.String(512))
        livestream_url = db.Column(db.String(512))
        youtube_embed_url = db.Column(db.String(512))
        
        featured_image = db.Column(db.String(512))
        published = db.Column(db.Boolean, default=False)
        
        author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


    class PageContent(db.Model):
        """Editable page content blocks"""
        id = db.Column(db.Integer, primary_key=True)
        page = db.Column(db.String(50), nullable=False, index=True)  # 'home', 'biography', 'schedule', 'media'
        section = db.Column(db.String(100), nullable=False)  # 'hero', 'featured', 'about', etc
        title = db.Column(db.String(255))
        content = db.Column(db.Text)
        image_url = db.Column(db.String(512))
        youtube_embed_url = db.Column(db.String(512))
        order = db.Column(db.Integer, default=0)
        published = db.Column(db.Boolean, default=True)
        
        author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
        
        __table_args__ = (
            db.UniqueConstraint('page', 'section', name='uq_page_section'),
        )


    class ContactMessage(db.Model):
        """Contact form submissions"""
        id = db.Column(db.Integer, primary_key=True)
        name = db.Column(db.String(255), nullable=False)
        email = db.Column(db.String(255), nullable=False)
        message = db.Column(db.Text, nullable=False)
        read = db.Column(db.Boolean, default=False)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    class Testimonial(db.Model):
        """Testimonials and reviews from collaborators, orchestras, and clients"""
        id = db.Column(db.Integer, primary_key=True)
        author = db.Column(db.String(255), nullable=False)  # Person/organisation name
        role = db.Column(db.String(255))  # e.g., "Conductor", "Festival Director", "Orchestra Manager"
        quote = db.Column(db.Text, nullable=False)  # The testimonial text
        organisation = db.Column(db.String(255))  # Optional: orchestra, band, festival name
        image_url = db.Column(db.String(512))  # Optional: headshot or logo
        order = db.Column(db.Integer, default=0)  # Display order in carousel
        published = db.Column(db.Boolean, default=True)
        
        author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
        
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    
    return {
        'User': User,
        'NewsItem': NewsItem,
        'Event': Event,
        'PageContent': PageContent,
        'ContactMessage': ContactMessage,
        'Testimonial': Testimonial,
    }
