/**
 * Inline Page Editor with TinyMCE
 * Adds pencil icons to editable page sections for inline rich-text editing
 */

(function() {
  'use strict';

  class PageEditor {
    constructor() {
      this.isAdmin = this.detectAdminStatus();
      this.editingElement = null;
      this.editorModal = null;

      if (this.isAdmin) {
        this.init();
      }
    }

    /**
     * Detect if user is logged in as admin
     */
    detectAdminStatus() {
      // Check if there's an admin indicator in the page (you could add a data attribute to <body>)
      const body = document.querySelector('body');
      return body && body.classList.contains('is-admin');
    }

    /**
     * Initialize the editor
     */
    init() {
      console.log('PageEditor: Admin detected, initializing...');
      this.setupTinyMCE();
      this.addEditButtons();
      this.setupModalEditor();
    }

    /**
     * Setup TinyMCE configuration
     */
    setupTinyMCE() {
      if (typeof tinymce === 'undefined') {
        console.warn('TinyMCE not loaded, skipping editor setup');
        return;
      }

      tinymce.init({
        selector: '#page-editor-textarea',
        plugins: [
          'advlist', 'autolink', 'lists', 'link', 'image', 'charmap',
          'anchor', 'searchreplace', 'visualblocks', 'code', 'fullscreen',
          'insertdatetime', 'media', 'table', 'paste', 'help', 'wordcount'
        ],
        toolbar: 'undo redo | formatselect | bold italic underline strikethrough | ' +
          'forecolor backcolor | alignleft aligncenter alignright alignjustify | ' +
          'bullist numlist outdent indent | link image | ' +
          'removeformat | fullscreen | help',
        height: 400,
        menubar: false,
        statusbar: true,
        branding: false,
        content_css: '/assets/css/main.css',
      });
    }

    /**
     * Add edit buttons to editable sections
     */
    addEditButtons() {
      const editables = document.querySelectorAll('[data-editable]');
      editables.forEach((el) => {
        // Create edit button wrapper
        const wrapper = document.createElement('div');
        wrapper.className = 'editable-wrapper';
        wrapper.style.position = 'relative';
        wrapper.style.display = 'inline-block';
        wrapper.style.width = el.offsetWidth ? 'auto' : '100%';

        // Insert wrapper before element
        el.parentNode.insertBefore(wrapper, el);
        wrapper.appendChild(el);

        // Create edit button (pencil icon)
        const editBtn = document.createElement('button');
        editBtn.type = 'button';
        editBtn.className = 'edit-btn';
        editBtn.title = 'Edit this section';
        editBtn.innerHTML = '<i class="fas fa-pencil-alt"></i>';
        editBtn.style.position = 'absolute';
        editBtn.style.top = '5px';
        editBtn.style.right = '5px';
        editBtn.style.background = '#007bff';
        editBtn.style.color = 'white';
        editBtn.style.border = 'none';
        editBtn.style.borderRadius = '4px';
        editBtn.style.padding = '6px 10px';
        editBtn.style.cursor = 'pointer';
        editBtn.style.fontSize = '14px';
        editBtn.style.opacity = '0';
        editBtn.style.transition = 'opacity 0.3s';
        editBtn.style.zIndex = '100';

        wrapper.appendChild(editBtn);

        // Show/hide edit button on hover
        wrapper.addEventListener('mouseenter', () => {
          editBtn.style.opacity = '1';
        });
        wrapper.addEventListener('mouseleave', () => {
          editBtn.style.opacity = '0';
        });

        // Add click handler
        editBtn.addEventListener('click', (e) => {
          e.preventDefault();
          this.openEditor(el);
        });
      });
    }

    /**
     * Setup the modal editor
     */
    setupModalEditor() {
      // Create modal HTML
      const modal = document.createElement('div');
      modal.id = 'page-editor-modal';
      modal.className = 'modal fade';
      modal.style.display = 'none';
      modal.innerHTML = `
        <div style="position: fixed; top: 0; left: 0; width: 100%; height: 100%; 
                    background: rgba(0,0,0,0.5); z-index: 999; display: flex; 
                    align-items: center; justify-content: center;">
          <div style="background: white; border-radius: 8px; box-shadow: 0 4px 20px rgba(0,0,0,0.15);
                      width: 90%; max-width: 900px; max-height: 90vh; overflow: auto; padding: 30px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid #eee; padding-bottom: 15px;">
              <h3 style="margin: 0; font-size: 24px; font-weight: 600;">Edit Content</h3>
              <button id="close-editor-modal" type="button" style="background: none; border: none; font-size: 28px; cursor: pointer; color: #666;">&times;</button>
            </div>
            <textarea id="page-editor-textarea" style="width: 100%; min-height: 400px;"></textarea>
            <div style="margin-top: 20px; display: flex; gap: 10px; justify-content: flex-end;">
              <button id="cancel-editor-btn" type="button" style="padding: 10px 20px; border: 1px solid #ccc; background: white; border-radius: 4px; cursor: pointer;">Cancel</button>
              <button id="save-editor-btn" type="button" style="padding: 10px 20px; background: #007bff; color: white; border: none; border-radius: 4px; cursor: pointer; font-weight: 600;">Save Changes</button>
            </div>
          </div>
        </div>
      `;

      document.body.appendChild(modal);
      this.editorModal = modal;

      // Setup event listeners
      document.getElementById('close-editor-modal').addEventListener('click', () => this.closeEditor());
      document.getElementById('cancel-editor-btn').addEventListener('click', () => this.closeEditor());
      document.getElementById('save-editor-btn').addEventListener('click', () => this.saveContent());

      // Close on backdrop click
      modal.addEventListener('click', (e) => {
        if (e.target === modal.querySelector('[style*="position: fixed"]')) {
          this.closeEditor();
        }
      });

      // Close on Escape key
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
          this.closeEditor();
        }
      });
    }

    /**
     * Open editor for an element
     */
    openEditor(element) {
      this.editingElement = element;
      const textarea = document.getElementById('page-editor-textarea');
      
      // Get content - use innerHTML to preserve HTML formatting
      const content = element.innerHTML;
      textarea.value = content;

      // Set TinyMCE content if loaded
      if (typeof tinymce !== 'undefined' && tinymce.get('page-editor-textarea')) {
        tinymce.get('page-editor-textarea').setContent(content);
      }

      // Show modal
      this.editorModal.style.display = 'flex';
      document.body.style.overflow = 'hidden';

      // Focus textarea
      setTimeout(() => {
        textarea.focus();
      }, 100);
    }

    /**
     * Close editor
     */
    closeEditor() {
      this.editingElement = null;
      this.editorModal.style.display = 'none';
      document.body.style.overflow = 'auto';

      // Clear TinyMCE
      if (typeof tinymce !== 'undefined' && tinymce.get('page-editor-textarea')) {
        tinymce.get('page-editor-textarea').setContent('');
      }
    }

    /**
     * Save content
     */
    async saveContent() {
      if (!this.editingElement) return;

      let content;
      
      // Get content from TinyMCE if available, otherwise from textarea
      if (typeof tinymce !== 'undefined' && tinymce.get('page-editor-textarea')) {
        content = tinymce.get('page-editor-textarea').getContent();
      } else {
        content = document.getElementById('page-editor-textarea').value;
      }

      // Get page and section identifiers
      const page = this.editingElement.dataset.page || 'unknown';
      const section = this.editingElement.dataset.section || 'unknown';

      try {
          const response = await fetch('/admin/api/page-content', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            page,
            section,
            content,
          }),
        });

        if (!response.ok) {
          const error = await response.json();
          alert(`Error saving: ${error.error || 'Unknown error'}`);
          return;
        }

        // Update the element with new content
        this.editingElement.innerHTML = content;

        // Show success message
        this.showNotification('Content saved successfully!', 'success');

        // Close editor
        this.closeEditor();
      } catch (error) {
        console.error('Error saving content:', error);
        alert(`Error saving content: ${error.message}`);
      }
    }

    /**
     * Show notification
     */
    showNotification(message, type = 'info') {
      const notification = document.createElement('div');
      notification.style.cssText = `
        position: fixed;
        top: 20px;
        right: 20px;
        padding: 15px 20px;
        background: ${type === 'success' ? '#28a745' : '#17a2b8'};
        color: white;
        border-radius: 4px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.2);
        z-index: 10000;
        animation: slideIn 0.3s ease-out;
      `;
      notification.textContent = message;

      document.body.appendChild(notification);

      setTimeout(() => {
        notification.style.animation = 'slideOut 0.3s ease-out';
        setTimeout(() => {
          notification.remove();
        }, 300);
      }, 3000);
    }
  }

  // Initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      new PageEditor();
    });
  } else {
    new PageEditor();
  }

  // Add CSS animations
  const style = document.createElement('style');
  style.textContent = `
    @keyframes slideIn {
      from {
        transform: translateX(400px);
        opacity: 0;
      }
      to {
        transform: translateX(0);
        opacity: 1;
      }
    }

    @keyframes slideOut {
      from {
        transform: translateX(0);
        opacity: 1;
      }
      to {
        transform: translateX(400px);
        opacity: 0;
      }
    }

    .editable-wrapper:hover {
      outline: 2px dashed #007bff;
      outline-offset: 2px;
    }
  `;
  document.head.appendChild(style);
})();
