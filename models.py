"""
Database models for Jack Capstaff CMS
"""
from datetime import datetime, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


def init_models(db):
    """Initialize database models with SQLAlchemy instance"""
    
    class SiteSetting(db.Model):
        """Key-value store for site-wide settings (e.g., shipping fee, free delivery threshold)."""
        id = db.Column(db.Integer, primary_key=True)
        key = db.Column(db.String(64), unique=True, nullable=False, index=True)
        value = db.Column(db.String(255), nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


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
        products = db.relationship('Product', backref='author', lazy=True, cascade='all, delete-orphan')

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

    class Product(db.Model):
        """Sellable music product with optional PDF download and printed copy."""
        id = db.Column(db.Integer, primary_key=True)
        slug = db.Column(db.String(255), unique=True, nullable=False, index=True)
        title = db.Column(db.String(255), nullable=False)
        subtitle = db.Column(db.String(255))
        description = db.Column(db.Text)
        cover_image_url = db.Column(db.String(512))
        pdf_file_url = db.Column(db.String(512))
        youtube_url = db.Column(db.String(512))
        price_pdf_cents = db.Column(db.Integer)
        price_print_cents = db.Column(db.Integer)
        has_pdf = db.Column(db.Boolean, default=True, nullable=False)
        has_print = db.Column(db.Boolean, default=True, nullable=False)
        published = db.Column(db.Boolean, default=False, nullable=False, index=True)
        sort_order = db.Column(db.Integer, default=0, nullable=False)

        author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    class ShopOrder(db.Model):
        """Customer order created from Stripe checkout."""
        id = db.Column(db.Integer, primary_key=True)
        order_number = db.Column(db.String(32), unique=True, nullable=False, index=True)
        status = db.Column(db.String(32), default='pending', nullable=False, index=True)
        stripe_checkout_session_id = db.Column(db.String(255), unique=True, index=True)
        stripe_payment_intent_id = db.Column(db.String(255), index=True)
        currency = db.Column(db.String(8), default='gbp', nullable=False)
        total_cents = db.Column(db.Integer, default=0, nullable=False)
        customer_name = db.Column(db.String(255))
        customer_email = db.Column(db.String(255), nullable=False, index=True)
        shipping_name = db.Column(db.String(255))
        shipping_line1 = db.Column(db.String(255))
        shipping_line2 = db.Column(db.String(255))
        shipping_city = db.Column(db.String(255))
        shipping_state = db.Column(db.String(255))
        shipping_postal_code = db.Column(db.String(64))
        shipping_country = db.Column(db.String(64))
        has_physical_items = db.Column(db.Boolean, default=False, nullable=False)
        customer_email_sent = db.Column(db.Boolean, default=False, nullable=False)
        admin_email_sent = db.Column(db.Boolean, default=False, nullable=False)
        paid_at = db.Column(db.DateTime)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

        items = db.relationship('ShopOrderItem', backref='order', lazy=True, cascade='all, delete-orphan')

    class ShopOrderItem(db.Model):
        """Snapshot of product purchase at time of checkout."""
        id = db.Column(db.Integer, primary_key=True)
        order_id = db.Column(db.Integer, db.ForeignKey('shop_order.id'), nullable=False, index=True)
        product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False, index=True)
        title_snapshot = db.Column(db.String(255), nullable=False)
        delivery_format = db.Column(db.String(16), nullable=False)  # 'pdf' or 'print'
        quantity = db.Column(db.Integer, default=1, nullable=False)
        unit_price_cents = db.Column(db.Integer, default=0, nullable=False)
        line_total_cents = db.Column(db.Integer, default=0, nullable=False)
        pdf_file_url_snapshot = db.Column(db.String(512))
        download_access_limit = db.Column(db.Integer, default=3, nullable=False)
        download_access_count = db.Column(db.Integer, default=0, nullable=False)
        first_downloaded_at = db.Column(db.DateTime)
        last_downloaded_at = db.Column(db.DateTime)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

        product = db.relationship('Product')

    class PublishingQuote(db.Model):
        """Saved publishing quote requests/results."""
        id = db.Column(db.Integer, primary_key=True)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True, index=True)
        title = db.Column(db.String(255), index=True)
        customer_email = db.Column(db.String(255), index=True)
        quote_payload = db.Column(db.Text, nullable=False)
        total_gbp = db.Column(db.Float, nullable=False, default=0.0)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    class PublishingOrder(db.Model):
        """Paid publishing quote order records."""
        id = db.Column(db.Integer, primary_key=True)
        order_number = db.Column(db.String(32), unique=True, nullable=False, index=True)
        status = db.Column(db.String(32), default='pending', nullable=False, index=True)
        stripe_checkout_session_id = db.Column(db.String(255), unique=True, index=True)
        stripe_payment_intent_id = db.Column(db.String(255), index=True)
        quote_id = db.Column(db.Integer, db.ForeignKey('publishing_quote.id'), index=True)
        user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
        title = db.Column(db.String(255), index=True)
        customer_name = db.Column(db.String(255))
        customer_email = db.Column(db.String(255), nullable=False, index=True)
        quote_payload = db.Column(db.Text, nullable=False)
        total_gbp = db.Column(db.Float, nullable=False, default=0.0)
        admin_email_sent = db.Column(db.Boolean, default=False, nullable=False)
        paid_at = db.Column(db.DateTime)
        created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
        updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    
    return {
        'SiteSetting': SiteSetting,
        'User': User,
        'NewsItem': NewsItem,
        'Event': Event,
        'PageContent': PageContent,
        'ContactMessage': ContactMessage,
        'Testimonial': Testimonial,
        'Product': Product,
        'ShopOrder': ShopOrder,
        'ShopOrderItem': ShopOrderItem,
        'PublishingQuote': PublishingQuote,
        'PublishingOrder': PublishingOrder,
    }
