// Shared Navigation JavaScript for Cuttle Reports

// Global variables
let currentPage = 'home';
let isMenuOpen = false;

// Theme management
const THEME_CYCLE = { dark: 'midnight', midnight: 'light', light: 'dark' };
const THEME_ICONS = { dark: '🌙', midnight: '🌑', light: '☀️' };

function toggleTheme() {
    const body = document.body;
    const themeIcon = document.querySelector('.theme-icon');

    // ui_boot owns <html> + <body> classes and the shell broadcast, so let it
    // drive the switch — otherwise <html> stays on the old theme for a frame.
    if (window.CuttleUiBoot) {
        const next = THEME_CYCLE[localStorage.getItem('theme') || 'dark'] || 'midnight';
        window.CuttleUiBoot.applyTheme(next);
        if (themeIcon) themeIcon.textContent = THEME_ICONS[next];
        return;
    }

    let theme = 'dark';
    if (body.classList.contains('dark-mode')) {
        // Switch from dark to midnight
        body.classList.remove('dark-mode');
        body.classList.add('midnight-mode');
        if (themeIcon) {
            themeIcon.textContent = '🌑';
        }
        localStorage.setItem('theme', 'midnight');
        theme = 'midnight';
    } else if (body.classList.contains('midnight-mode')) {
        // Switch from midnight to light
        body.classList.remove('midnight-mode');
        body.classList.add('light-mode');
        if (themeIcon) {
            themeIcon.textContent = '☀️';
        }
        localStorage.setItem('theme', 'light');
        theme = 'light';
    } else {
        // Switch from light to dark
        body.classList.remove('light-mode');
        body.classList.add('dark-mode');
        if (themeIcon) {
            themeIcon.textContent = '🌙';
        }
        localStorage.setItem('theme', 'dark');
        theme = 'dark';
    }
    if (typeof window !== 'undefined' && window.parent !== window) {
        window.parent.postMessage({ type: 'cuttle-theme-change', theme }, '*');
    }
}

// Initialize theme from localStorage
function initializeTheme() {
    const savedTheme = localStorage.getItem('theme') || 'dark';
    const body = document.body;
    const themeIcon = document.querySelector('.theme-icon');

    // ui_boot.js already applied the correct theme before first paint. Only
    // sync the icon — re-removing classes here causes a visible theme flash.
    if (window.CuttleUiBoot) {
        if (themeIcon) {
            if (savedTheme === 'light') themeIcon.textContent = '☀️';
            else if (savedTheme === 'midnight') themeIcon.textContent = '🌑';
            else themeIcon.textContent = '🌙';
        }
        return;
    }

    body.classList.remove('dark-mode', 'midnight-mode', 'light-mode');

    if (savedTheme === 'light') {
        body.classList.add('light-mode');
        if (themeIcon) themeIcon.textContent = '☀️';
    } else if (savedTheme === 'midnight') {
        body.classList.add('midnight-mode');
        if (themeIcon) themeIcon.textContent = '🌑';
    } else {
        body.classList.add('dark-mode');
        if (themeIcon) themeIcon.textContent = '🌙';
    }
}

// Menu management
function toggleMenu() {
    const dropdown = document.getElementById('menuDropdown');
    if (!dropdown) return;
    
    isMenuOpen = !isMenuOpen;
    
    if (isMenuOpen) {
        dropdown.classList.add('active');
    } else {
        dropdown.classList.remove('active');
    }
}

// Close menu when clicking outside
function closeMenu() {
    const dropdown = document.getElementById('menuDropdown');
    if (dropdown && isMenuOpen) {
        dropdown.classList.remove('active');
        isMenuOpen = false;
    }
}

// Navigation functions
function showPage(pageName) {
    // Close menu
    closeMenu();
    
    // Handle navigation based on page
    switch(pageName) {
        case 'home':
            window.location.href = '/';
            break;
        case 'control':
            // For now, just close menu - could add control panel later
            break;
        case 'settings':
            // For now, just close menu - could add settings later
            break;
        case 'about':
            // For now, just close menu - could add about page later
            break;
        case 'reports':
            // For now, just close menu - could add reports listing later
            break;
        case 'budget':
            // Navigate to budget report if available
            break;
        case 'data':
            window.location.href = '/query_log.html';
            break;
        case 'query':
            window.location.href = '/query_log.html';
            break;
        case 'test':
            // Navigate to test reports if available
            break;
        default:
            console.log(`Navigation to ${pageName} not implemented yet`);
    }
}

// Logo click handler — when inside app_shell iframe, ask parent to navigate to chat
function goToHome() {
    if (window !== window.top && window.parent) {
        window.parent.postMessage({ type: 'cuttle-navigate', page: '/chat_page.html' }, '*');
    } else {
        window.location.href = '/';
    }
}

// Navigate to control panel
function goToControl() {
    window.location.href = '/control_panel.html';
}

// Navigate to settings
function goToSettings() {
    window.location.href = '/settings_page.html';
}

// Navigate to about page
function goToAbout() {
    window.location.href = '/about_page.html';
}

// Admin functions
function shutdownBot() {
    if (confirm('Are you sure you want to shutdown the Cuttle bot? This will stop all services.')) {
        // Close menu first
        closeMenu();
        
        // Show loading state
        const button = event.target;
        const originalText = button.textContent;
        button.textContent = '🔄 Shutting down...';
        button.disabled = true;
        
        // Make API call to shutdown
        fetch('/api/shutdown', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                button.textContent = '✅ Shutdown complete';
                setTimeout(() => {
                    window.location.href = '/';
                }, 2000);
            } else {
                button.textContent = '❌ Shutdown failed';
                button.disabled = false;
                setTimeout(() => {
                    button.textContent = originalText;
                }, 3000);
            }
        })
        .catch(error => {
            console.error('Shutdown error:', error);
            button.textContent = '❌ Shutdown failed';
            button.disabled = false;
            setTimeout(() => {
                button.textContent = originalText;
            }, 3000);
        });
    }
}

function restartBot() {
    if (confirm('Are you sure you want to restart the Cuttle bot? This will restart all services.')) {
        // Close menu first
        closeMenu();
        
        // Show loading state
        const button = event.target;
        const originalText = button.textContent;
        button.textContent = '🔄 Restarting...';
        button.disabled = true;
        
        // Make API call to restart
        fetch('/api/restart', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            }
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                button.textContent = '✅ Restart complete';
                setTimeout(() => {
                    window.location.reload();
                }, 2000);
            } else {
                button.textContent = '❌ Restart failed';
                button.disabled = false;
                setTimeout(() => {
                    button.textContent = originalText;
                }, 3000);
            }
        })
        .catch(error => {
            console.error('Restart error:', error);
            button.textContent = '❌ Restart failed';
            button.disabled = false;
            setTimeout(() => {
                button.textContent = originalText;
            }, 3000);
        });
    }
}

// Initialize everything when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    // Initialize theme
    initializeTheme();
    
    // Add click outside listener for menu
    document.addEventListener('click', function(event) {
        const menuToggle = document.querySelector('.menu-toggle');
        const menuDropdown = document.getElementById('menuDropdown');
        
        if (menuToggle && menuDropdown) {
            if (!menuToggle.contains(event.target) && !menuDropdown.contains(event.target)) {
                closeMenu();
            }
        }
    });
    
    // Add escape key listener
    document.addEventListener('keydown', function(event) {
        if (event.key === 'Escape') {
            closeMenu();
        }
    });
    
    // Initialize menu state
    currentPage = document.body.dataset.page || 'home';
});

// Export functions for use in other scripts
window.toggleTheme = toggleTheme;
window.toggleMenu = toggleMenu;
window.showPage = showPage;
window.closeMenu = closeMenu;
window.goToHome = goToHome;
window.goToControl = goToControl;
window.goToSettings = goToSettings;
window.goToAbout = goToAbout;
window.shutdownBot = shutdownBot;
window.restartBot = restartBot;
