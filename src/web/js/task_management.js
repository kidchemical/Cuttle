// Task Management JavaScript

class TaskManager {
    constructor() {
        this.tasks = [];
        this.currentView = 'list';
        this.currentTask = null;
        this.filters = {
            statuses: ['None', 'Planned', 'In Progress', 'Blocked'], // Default: exclude Closed tasks
            owners: [], // Changed to array for multiple selections
            priorities: [], // Changed to array for multiple selections
            search: ''
        };
        this.pagination = {
            currentPage: 1,
            itemsPerPage: 10,
            totalPages: 1
        };
        this.sorting = {
            field: 'createdAt',
            direction: 'desc'
        };
        this.init();
    }

    async init() {
        await this.loadTasks();
        this.setupEventListeners();
        this.updateStats();
        this.populateOwnerFilter();
        this.initializeDefaultFilters();
    }

    setupEventListeners() {
        // Search functionality
        const searchInput = document.getElementById('taskSearch');
        if (searchInput) {
            searchInput.addEventListener('input', (e) => {
                this.filters.search = e.target.value;
                this.filterTasks();
            });
        }

        // Status, Owner, and Priority filters are now handled by their respective update functions

        // Modal close on outside click
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('modal')) {
                this.closeAllModals();
            }
            // Close dropdowns when clicking outside (but not on checkboxes/labels)
            if (e.target.type !== 'checkbox' && e.target.tagName !== 'LABEL') {
                if (!e.target.closest('.priority-filter-container')) {
                    this.closePriorityDropdown();
                }
                if (!e.target.closest('.status-filter-container')) {
                    const statusPortal = document.getElementById('statusFilterPortal');
                    if (statusPortal) {
                        statusPortal.remove();
                        document.getElementById('statusFilter').classList.remove('active');
                    }
                }
                if (!e.target.closest('.owner-filter-container')) {
                    const ownerPortal = document.getElementById('ownerFilterPortal');
                    if (ownerPortal) {
                        ownerPortal.remove();
                        document.getElementById('ownerFilter').classList.remove('active');
                    }
                }
            }
        });

        // Escape key to close modals
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.closeAllModals();
            }
        });

        // Handle window resize to close all dropdowns
        window.addEventListener('resize', () => {
            this.closePriorityDropdown();
            const statusPortal = document.getElementById('statusFilterPortal');
            const ownerPortal = document.getElementById('ownerFilterPortal');
            
            if (statusPortal) {
                statusPortal.remove();
                document.getElementById('statusFilter').classList.remove('active');
            }
            if (ownerPortal) {
                ownerPortal.remove();
                document.getElementById('ownerFilter').classList.remove('active');
            }
        });

        // Handle scroll to close all dropdowns (common UX pattern)
        window.addEventListener('scroll', () => {
            const priorityPortal = document.getElementById('priorityFilterPortal');
            const statusPortal = document.getElementById('statusFilterPortal');
            const ownerPortal = document.getElementById('ownerFilterPortal');
            
            if (priorityPortal) {
                this.closePriorityDropdown();
            }
            if (statusPortal) {
                statusPortal.remove();
                document.getElementById('statusFilter').classList.remove('active');
            }
            if (ownerPortal) {
                ownerPortal.remove();
                document.getElementById('ownerFilter').classList.remove('active');
            }
        });

    }

    async loadTasks() {
        try {
            const response = await fetch('/api/tasks');
            if (response.ok) {
                this.tasks = await response.json();
            } else {
                console.error('Failed to load tasks:', response.statusText);
                this.tasks = this.getMockTasks(); // Fallback to mock data
            }
        } catch (error) {
            console.error('Error loading tasks:', error);
            this.tasks = this.getMockTasks(); // Fallback to mock data
        }
        this.renderTasks();
    }

    getMockTasks() {
        return [
            {
                id: 1,
                taskId: 'TASK-001',
                title: 'Implement user authentication',
                description: 'Add login and registration functionality to the web application',
                owner: 'John Doe',
                status: 'In Progress',
                priority: 'High',
                createdAt: '2024-01-15T10:00:00Z',
                updatedAt: '2024-01-20T14:30:00Z',
                comments: [
                    {
                        id: 1,
                        author: 'John Doe',
                        content: 'Started working on the authentication system',
                        createdAt: '2024-01-15T10:00:00Z'
                    },
                    {
                        id: 2,
                        author: 'Jane Smith',
                        content: 'Make sure to include password reset functionality',
                        createdAt: '2024-01-16T09:15:00Z'
                    }
                ]
            },
            {
                id: 2,
                taskId: 'TASK-002',
                title: 'Fix responsive design issues',
                description: 'Address mobile layout problems on the dashboard page',
                owner: 'Jane Smith',
                status: 'Planned',
                priority: 'Medium',
                createdAt: '2024-01-18T11:00:00Z',
                updatedAt: '2024-01-18T11:00:00Z',
                comments: []
            },
            {
                id: 3,
                taskId: 'TASK-003',
                title: 'Database optimization',
                description: 'Optimize database queries for better performance',
                owner: 'Mike Johnson',
                status: 'Blocked',
                priority: 'High',
                createdAt: '2024-01-10T08:00:00Z',
                updatedAt: '2024-01-19T16:45:00Z',
                comments: [
                    {
                        id: 3,
                        author: 'Mike Johnson',
                        content: 'Waiting for database access credentials',
                        createdAt: '2024-01-19T16:45:00Z'
                    }
                ]
            },
            {
                id: 4,
                taskId: 'TASK-004',
                title: 'Add dark mode support',
                description: 'Implement dark/light theme toggle functionality',
                owner: 'Sarah Wilson',
                status: 'Closed',
                priority: 'Low',
                createdAt: '2024-01-05T14:00:00Z',
                updatedAt: '2024-01-17T12:00:00Z',
                comments: [
                    {
                        id: 4,
                        author: 'Sarah Wilson',
                        content: 'Completed dark mode implementation',
                        createdAt: '2024-01-17T12:00:00Z'
                    }
                ]
            },
            {
                id: 5,
                taskId: 'TASK-005',
                title: 'Security vulnerability fix',
                description: 'Fix critical security issue in authentication system',
                owner: 'Alex Chen',
                status: 'In Progress',
                priority: 'Critical',
                createdAt: '2024-01-20T09:00:00Z',
                updatedAt: '2024-01-20T15:30:00Z',
                comments: [
                    {
                        id: 5,
                        author: 'Alex Chen',
                        content: 'Working on the security patch',
                        createdAt: '2024-01-20T09:00:00Z'
                    }
                ]
            },
            {
                id: 6,
                taskId: 'TASK-006',
                title: 'Update documentation',
                description: 'Update API documentation for new endpoints',
                owner: 'Emma Davis',
                status: 'Planned',
                priority: 'Low',
                createdAt: '2024-01-19T11:00:00Z',
                updatedAt: '2024-01-19T11:00:00Z',
                comments: []
            },
            {
                id: 7,
                taskId: 'TASK-007',
                title: 'Performance optimization',
                description: 'Optimize database queries and improve response times',
                owner: 'David Kim',
                status: 'In Progress',
                priority: 'High',
                createdAt: '2024-01-18T13:00:00Z',
                updatedAt: '2024-01-20T10:15:00Z',
                comments: [
                    {
                        id: 6,
                        author: 'David Kim',
                        content: 'Identified slow queries, working on optimization',
                        createdAt: '2024-01-20T10:15:00Z'
                    }
                ]
            }
        ];
    }

    renderTasks() {
        const filteredTasks = this.getFilteredTasks();
        
        if (this.currentView === 'list') {
            this.renderListView(filteredTasks);
        } else {
            this.renderKanbanView(filteredTasks);
        }
        
        this.updateStats();
    }

    getFilteredTasks() {
        const filtered = this.tasks.filter(task => {
            const matchesStatuses = this.filters.statuses.length === 0 || this.filters.statuses.includes(task.status);
            const matchesOwners = this.filters.owners.length === 0 || this.filters.owners.includes(task.owner);
            const matchesPriorities = this.filters.priorities.length === 0 || this.filters.priorities.includes(task.priority);
            const matchesSearch = !this.filters.search || 
                task.title.toLowerCase().includes(this.filters.search.toLowerCase()) ||
                task.description.toLowerCase().includes(this.filters.search.toLowerCase()) ||
                task.taskId.toLowerCase().includes(this.filters.search.toLowerCase());
            
            return matchesStatuses && matchesOwners && matchesPriorities && matchesSearch;
        });
        
        return this.sortTasks(filtered);
    }

    sortTasks(tasks) {
        return tasks.sort((a, b) => {
            let aValue, bValue;
            
            switch (this.sorting.field) {
                case 'priority':
                    const priorityOrder = { 'Critical': 4, 'High': 3, 'Medium': 2, 'Low': 1 };
                    aValue = priorityOrder[a.priority] || 0;
                    bValue = priorityOrder[b.priority] || 0;
                    break;
                case 'createdAt':
                    aValue = new Date(a.createdAt).getTime();
                    bValue = new Date(b.createdAt).getTime();
                    break;
                case 'title':
                    aValue = a.title.toLowerCase();
                    bValue = b.title.toLowerCase();
                    break;
                default:
                    return 0;
            }
            
            if (this.sorting.direction === 'asc') {
                return aValue > bValue ? 1 : aValue < bValue ? -1 : 0;
            } else {
                return aValue < bValue ? 1 : aValue > bValue ? -1 : 0;
            }
        });
    }

    renderListView(tasks) {
        const tasksGrid = document.getElementById('tasksGrid');
        const noTasks = document.getElementById('noTasks');
        const tasksLoading = document.getElementById('tasksLoading');
        const pagination = document.getElementById('pagination');
        
        if (tasksLoading) tasksLoading.style.display = 'none';
        
        if (tasks.length === 0) {
            if (noTasks) noTasks.style.display = 'block';
            if (tasksGrid) tasksGrid.innerHTML = '';
            if (pagination) pagination.style.display = 'none';
            return;
        }
        
        if (noTasks) noTasks.style.display = 'none';
        
        // Calculate pagination
        this.pagination.totalPages = Math.ceil(tasks.length / this.pagination.itemsPerPage);
        this.pagination.currentPage = Math.min(this.pagination.currentPage, this.pagination.totalPages);
        
        // Get tasks for current page
        const startIndex = (this.pagination.currentPage - 1) * this.pagination.itemsPerPage;
        const endIndex = startIndex + this.pagination.itemsPerPage;
        const pageTasks = tasks.slice(startIndex, endIndex);
        
        if (tasksGrid) {
            tasksGrid.innerHTML = pageTasks.map(task => this.createTaskCard(task)).join('');
        }
        
        // Update pagination controls
        this.updatePaginationControls(tasks.length);
    }

    renderKanbanView(tasks) {
        const statuses = ['None', 'Planned', 'In Progress', 'Blocked', 'Closed'];
        
        statuses.forEach(status => {
            const column = document.querySelector(`[data-status="${status}"]`);
            const countElement = column?.querySelector('.task-count');
            const tasksContainer = document.getElementById(`kanban${status.replace(' ', '')}`);
            
            if (!column || !tasksContainer) {
                return;
            }
            
            const statusTasks = tasks.filter(task => task.status === status);
            
            if (countElement) {
                countElement.textContent = statusTasks.length;
            }
            
            tasksContainer.innerHTML = statusTasks.map(task => this.createKanbanTaskCard(task)).join('');
        });
    }

    createTaskCard(task) {
        const createdDate = new Date(task.createdAt).toLocaleDateString();
        const updatedDate = new Date(task.updatedAt).toLocaleDateString();
        
        return `
            <div class="task-card" onclick="taskManager.showTaskDetail(${task.id})">
                <div class="task-card-content">
                    <div class="task-card-header">
                        <div class="task-id">${task.taskId}</div>
                        <div class="task-status-badge status-${task.status.toLowerCase().replace(' ', '-')}">${task.status}</div>
                    </div>
                    <div class="task-card-main">
                        <h3 class="task-title">${this.escapeHtml(task.title)}</h3>
                        <p class="task-description">${this.escapeHtml(task.description)}</p>
                    </div>
                </div>
                <div class="task-card-actions">
                    <div class="task-meta">
                        <div class="task-owner">
                            <span>👤</span>
                            <span>${this.escapeHtml(task.owner || 'Unassigned')}</span>
                        </div>
                        <div class="task-priority priority-${task.priority.toLowerCase()}">${task.priority}</div>
                        <div class="task-date">${updatedDate}</div>
                    </div>
                </div>
            </div>
        `;
    }

    createKanbanTaskCard(task) {
        return `
            <div class="kanban-task-card" 
                 draggable="true" 
                 data-task-id="${task.id}"
                 data-task-status="${task.status}"
                 onclick="taskManager.handleKanbanCardClick(event, ${task.id})"
                 ondragstart="taskManager.handleDragStart(event)"
                 ondragend="taskManager.handleDragEnd(event)">
                <div class="task-id">${task.taskId}</div>
                <h3 class="task-title">${this.escapeHtml(task.title)}</h3>
                <p class="task-description">${this.escapeHtml(task.description)}</p>
                <div class="task-meta">
                    <div class="task-owner">👤 ${this.escapeHtml(task.owner || 'Unassigned')}</div>
                    <div class="task-priority priority-${task.priority.toLowerCase()}">${task.priority}</div>
                </div>
            </div>
        `;
    }

    updateStats() {
        const openTasks = this.tasks.filter(task => task.status !== 'Closed').length;
        const inProgressTasks = this.tasks.filter(task => task.status === 'In Progress').length;
        const blockedTasks = this.tasks.filter(task => task.status === 'Blocked').length;
        const closedTasks = this.tasks.filter(task => task.status === 'Closed').length;
        
        this.updateStatElement('openTasks', openTasks);
        this.updateStatElement('inProgressTasks', inProgressTasks);
        this.updateStatElement('blockedTasks', blockedTasks);
        this.updateStatElement('closedTasks', closedTasks);
    }

    updateStatElement(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    }

    populateOwnerFilter() {
        const ownerOptions = document.getElementById('ownerFilterOptions');
        if (!ownerOptions) return;
        
        const owners = [...new Set(this.tasks.map(task => task.owner).filter(owner => owner))];
        
        ownerOptions.innerHTML = owners.map(owner => `
            <label class="multiselect-option">
                <input type="checkbox" value="${this.escapeHtml(owner)}" onchange="updateOwnerFilter()" class="multiselect-toggle-input">
                <span class="multiselect-toggle-track"><span class="multiselect-toggle-thumb"></span></span>
                <span class="owner-name">${this.escapeHtml(owner)}</span>
            </label>
        `).join('');
    }

    initializeDefaultFilters() {
        // Set up default status filter checkboxes
        const statusCheckboxes = document.querySelectorAll('#statusFilterOptions input[type="checkbox"]');
        statusCheckboxes.forEach(checkbox => {
            if (this.filters.statuses.includes(checkbox.value)) {
                checkbox.checked = true;
            }
        });
        
        // Update the status filter text to reflect the default selection
        this.updateStatusFilterText();
        
        // Apply the default filter
        this.filterTasks();
    }

    showCreateTaskModal() {
        this.currentTask = null;
        document.getElementById('modalTitle').textContent = 'Create Task';
        document.getElementById('taskForm').reset();
        document.getElementById('taskModal').classList.add('active');
    }

    showTaskDetail(taskId) {
        this.currentTask = this.tasks.find(task => task.id === taskId);
        if (!this.currentTask) return;
        
        this.populateTaskDetailModal();
        document.getElementById('taskDetailModal').classList.add('active');
    }

    populateTaskDetailModal() {
        if (!this.currentTask) return;
        
        document.getElementById('taskDetailId').textContent = this.currentTask.taskId;
        document.getElementById('taskDetailTitleText').textContent = this.currentTask.title;
        document.getElementById('taskDetailDescription').textContent = this.currentTask.description;
        document.getElementById('taskDetailOwner').textContent = this.currentTask.owner || 'Unassigned';
        document.getElementById('taskDetailPriority').textContent = this.currentTask.priority;
        document.getElementById('taskDetailCreated').textContent = new Date(this.currentTask.createdAt).toLocaleDateString();
        document.getElementById('taskDetailUpdated').textContent = new Date(this.currentTask.updatedAt).toLocaleDateString();
        
        const statusBadge = document.getElementById('taskDetailStatus');
        statusBadge.textContent = this.currentTask.status;
        statusBadge.className = `task-status-badge status-${this.currentTask.status.toLowerCase().replace(' ', '-')}`;
        
        this.renderComments();
        
        // Setup comment input event listener
        const commentInput = document.getElementById('newComment');
        if (commentInput) {
            // Remove any existing event listeners to avoid duplicates
            commentInput.removeEventListener('keydown', this.handleCommentKeydown);
            
            // Add new event listener
            this.handleCommentKeydown = (e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this.addComment();
                }
            };
            commentInput.addEventListener('keydown', this.handleCommentKeydown);
        }
    }

    renderComments() {
        const commentsList = document.getElementById('commentsList');
        if (!commentsList || !this.currentTask) return;
        
        const comments = this.currentTask.comments || [];
        
        if (comments.length === 0) {
            commentsList.innerHTML = '<p class="no-comments">No comments yet. Be the first to add one!</p>';
            return;
        }
        
        commentsList.innerHTML = comments.map(comment => `
            <div class="comment-item">
                <div class="comment-header">
                    <span class="comment-author">${this.escapeHtml(comment.author)}</span>
                    <span class="comment-date">${new Date(comment.createdAt).toLocaleString()}</span>
                    <button class="comment-delete-btn" onclick="taskManager.deleteComment(${comment.id})" title="Delete comment">
                        🗑️
                    </button>
                </div>
                <div class="comment-content">${this.escapeHtml(comment.content)}</div>
            </div>
        `).join('');
    }

    async saveTask() {
        const form = document.getElementById('taskForm');
        const formData = new FormData(form);
        
        const taskData = {
            title: formData.get('title'),
            description: formData.get('description'),
            owner: formData.get('owner'),
            status: formData.get('status'),
            priority: formData.get('priority')
        };
        
        if (!taskData.title.trim()) {
            (window.showToast || alert)('Please enter a task title', 'warning');
            return;
        }
        
        try {
            let response;
            if (this.currentTask) {
                // Update existing task
                response = await fetch(`/api/tasks/${this.currentTask.id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(taskData)
                });
            } else {
                // Create new task
                response = await fetch('/api/tasks', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(taskData)
                });
            }
            
            if (response.ok) {
                await this.loadTasks();
                this.closeTaskModal();
                this.populateOwnerFilter();
            } else {
                throw new Error('Failed to save task');
            }
        } catch (error) {
            console.error('Error saving task:', error);
            (window.showToast || alert)('Failed to save task. Please try again.', 'error');
        }
    }

    async deleteCurrentTask() {
        if (!this.currentTask) return;
        
        if (!confirm(`Are you sure you want to delete task ${this.currentTask.taskId}?`)) {
            return;
        }
        
        try {
            const response = await fetch(`/api/tasks/${this.currentTask.id}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                await this.loadTasks();
                this.closeTaskDetailModal();
                this.populateOwnerFilter();
            } else {
                throw new Error('Failed to delete task');
            }
        } catch (error) {
            console.error('Error deleting task:', error);
            (window.showToast || alert)('Failed to delete task. Please try again.', 'error');
        }
    }

    editCurrentTask() {
        if (!this.currentTask) return;
        
        document.getElementById('modalTitle').textContent = 'Edit Task';
        document.getElementById('taskTitle').value = this.currentTask.title;
        document.getElementById('taskDescription').value = this.currentTask.description;
        document.getElementById('taskOwner').value = this.currentTask.owner || '';
        document.getElementById('taskStatus').value = this.currentTask.status;
        document.getElementById('taskPriority').value = this.currentTask.priority;
        
        this.closeTaskDetailModal();
        document.getElementById('taskModal').classList.add('active');
    }

    async addComment() {
        const commentInput = document.getElementById('newComment');
        const content = commentInput.value.trim();
        
        if (!content) {
            (window.showToast || alert)('Please enter a comment', 'warning');
            return;
        }
        
        if (!this.currentTask) return;
        
        // Create the new comment object
        const newComment = {
            id: Date.now(), // Temporary ID until server responds
            author: this.getCurrentUser(), // Get current user name
            content: content,
            createdAt: new Date().toISOString()
        };
        
        // Add comment to local task data immediately
        if (!this.currentTask.comments) {
            this.currentTask.comments = [];
        }
        this.currentTask.comments.push(newComment);
        
        // Update the UI immediately
        this.renderComments();
        commentInput.value = '';
        
        // Show a brief success indicator
        this.showCommentSuccessIndicator();
        
        try {
            const response = await fetch(`/api/tasks/${this.currentTask.id}/comments`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ content })
            });
            
            if (response.ok) {
                // Update the task in our local tasks array
                const taskIndex = this.tasks.findIndex(task => task.id === this.currentTask.id);
                if (taskIndex !== -1) {
                    this.tasks[taskIndex] = { ...this.currentTask };
                }
            } else {
                // If server request failed, remove the comment from local data
                this.currentTask.comments.pop();
                this.renderComments();
                this.showCommentErrorIndicator();
                throw new Error('Failed to add comment');
            }
        } catch (error) {
            console.error('Error adding comment:', error);
            // Only show alert if we haven't already shown the error indicator
            if (!this.currentTask.comments.some(comment => comment.id === newComment.id)) {
                this.showCommentErrorIndicator();
            }
        }
    }

    async deleteComment(commentId) {
        if (!this.currentTask || !commentId) return;
        
        // Find the comment
        const commentIndex = this.currentTask.comments.findIndex(comment => comment.id === commentId);
        if (commentIndex === -1) return;
        
        const comment = this.currentTask.comments[commentIndex];
        
        // Show confirmation dialog
        if (!confirm(`Are you sure you want to delete this comment?\n\n"${comment.content}"`)) {
            return;
        }
        
        // Remove comment from local data immediately
        this.currentTask.comments.splice(commentIndex, 1);
        this.renderComments();
        
        try {
            const response = await fetch(`/api/tasks/${this.currentTask.id}/comments/${commentId}`, {
                method: 'DELETE'
            });
            
            if (response.ok) {
                // Update the task in our local tasks array
                const taskIndex = this.tasks.findIndex(task => task.id === this.currentTask.id);
                if (taskIndex !== -1) {
                    this.tasks[taskIndex] = { ...this.currentTask };
                }
                this.showCommentSuccessIndicator('Comment deleted');
            } else {
                // If server request failed, restore the comment
                this.currentTask.comments.splice(commentIndex, 0, comment);
                this.renderComments();
                this.showCommentErrorIndicator('Failed to delete comment');
                throw new Error('Failed to delete comment');
            }
        } catch (error) {
            console.error('Error deleting comment:', error);
            // Restore comment if it was removed
            if (!this.currentTask.comments.some(c => c.id === commentId)) {
                this.currentTask.comments.splice(commentIndex, 0, comment);
                this.renderComments();
            }
            this.showCommentErrorIndicator('Failed to delete comment');
        }
    }

    // Drag and Drop functionality for Kanban view
    handleDragStart(event) {
        const taskCard = event.target.closest('.kanban-task-card');
        if (!taskCard) return;
        
        const taskId = parseInt(taskCard.dataset.taskId);
        const task = this.tasks.find(t => t.id === taskId);
        
        if (!task) return;
        
        // Store the dragged task data
        event.dataTransfer.setData('text/plain', JSON.stringify({
            taskId: task.id,
            currentStatus: task.status
        }));
        
        // Add visual feedback
        taskCard.style.opacity = '0.5';
        taskCard.classList.add('dragging');
        
        // Store reference to dragged element
        this.draggedElement = taskCard;
    }

    handleDragEnd(event) {
        const taskCard = event.target.closest('.kanban-task-card');
        if (!taskCard) return;
        
        // Remove visual feedback
        taskCard.style.opacity = '';
        taskCard.classList.remove('dragging');
        
        // Remove drop zone highlighting
        document.querySelectorAll('.kanban-column').forEach(column => {
            column.classList.remove('drag-over');
        });
        
        // Clear dragged element reference after a short delay to prevent click events
        setTimeout(() => {
            this.draggedElement = null;
        }, 100);
    }

    handleDragOver(event) {
        event.preventDefault();
        
        const column = event.currentTarget;
        column.classList.add('drag-over');
    }

    async handleDrop(event) {
        event.preventDefault();
        
        const column = event.currentTarget;
        const newStatus = column.dataset.status;
        
        // Remove drop zone highlighting
        document.querySelectorAll('.kanban-column').forEach(col => {
            col.classList.remove('drag-over');
        });
        
        try {
            // Get the dragged task data
            const dragData = JSON.parse(event.dataTransfer.getData('text/plain'));
            const taskId = dragData.taskId;
            const currentStatus = dragData.currentStatus;
            
            // Don't do anything if dropped in the same column
            if (currentStatus === newStatus) {
                return;
            }
            
            // Find the task
            const task = this.tasks.find(t => t.id === taskId);
            if (!task) return;
            
            // Update task status locally immediately
            const oldStatus = task.status;
            task.status = newStatus;
            task.updatedAt = new Date().toISOString();
            
            // Update the UI immediately
            this.renderTasks();
            
            try {
                // Update task on server
                const response = await fetch(`/api/tasks/${taskId}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        title: task.title,
                        description: task.description,
                        owner: task.owner,
                        status: newStatus,
                        priority: task.priority
                    })
                });
                
                if (response.ok) {
                    this.showCommentSuccessIndicator(`Task moved to ${newStatus}`);
                } else {
                    // Revert the change if server update failed
                    task.status = oldStatus;
                    task.updatedAt = new Date().toISOString();
                    this.renderTasks();
                    this.showCommentErrorIndicator('Failed to update task status');
                    throw new Error('Failed to update task status');
                }
            } catch (error) {
                console.error('Error updating task status:', error);
                // Revert the change if server update failed
                task.status = oldStatus;
                task.updatedAt = new Date().toISOString();
                this.renderTasks();
                this.showCommentErrorIndicator('Failed to update task status');
            }
        } catch (error) {
            console.error('Error handling drop:', error);
            this.showCommentErrorIndicator('Failed to move task');
        }
    }

    handleKanbanCardClick(event, taskId) {
        // Don't open task detail if we just finished dragging
        if (this.draggedElement) {
            return;
        }
        
        // Check if this was a drag operation by looking for drag data
        if (event.dataTransfer && event.dataTransfer.getData('text/plain')) {
            return;
        }
        
        this.showTaskDetail(taskId);
    }

    showCommitCloseModal() {
        if (!this.currentTask) return;
        
        document.getElementById('commitMessage').value = `Close ${this.currentTask.taskId}: ${this.currentTask.title}`;
        this.updateCommitPreview();
        document.getElementById('commitCloseModal').classList.add('active');
    }

    updateCommitPreview() {
        const message = document.getElementById('commitMessage').value;
        const files = document.getElementById('commitFiles').value;
        
        let preview = `git add ${files || '.'}\n`;
        preview += `git commit -m "${message}"\n`;
        preview += `# This will close task ${this.currentTask?.taskId}`;
        
        document.getElementById('commitPreview').textContent = preview;
    }

    async executeCommitClose() {
        const message = document.getElementById('commitMessage').value.trim();
        const files = document.getElementById('commitFiles').value.trim();
        
        if (!message) {
            (window.showToast || alert)('Please enter a commit message', 'warning');
            return;
        }
        
        if (!this.currentTask) return;
        
        try {
            const response = await fetch(`/api/tasks/${this.currentTask.id}/close-via-commit`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message, files })
            });
            
            if (response.ok) {
                await this.loadTasks();
                this.closeCommitModal();
                this.closeTaskDetailModal();
                (window.showToast || alert)('Task closed successfully via commit!', 'success');
            } else {
                throw new Error('Failed to close task via commit');
            }
        } catch (error) {
            console.error('Error closing task via commit:', error);
            (window.showToast || alert)('Failed to close task via commit. Please try again.', 'error');
        }
    }

    closeTaskModal() {
        document.getElementById('taskModal').classList.remove('active');
    }

    closeTaskDetailModal() {
        document.getElementById('taskDetailModal').classList.remove('active');
    }

    closeCommitModal() {
        document.getElementById('commitCloseModal').classList.remove('active');
    }

    closeAllModals() {
        this.closeTaskModal();
        this.closeTaskDetailModal();
        this.closeCommitModal();
    }

    toggleView(view) {
        this.currentView = view;
        
        // Update view toggle buttons
        document.querySelectorAll('.view-toggle').forEach(btn => {
            btn.classList.remove('active');
        });
        document.querySelector(`[data-view="${view}"]`).classList.add('active');
        
        // Show/hide views
        document.getElementById('tasksListView').style.display = view === 'list' ? 'block' : 'none';
        document.getElementById('tasksKanbanView').style.display = view === 'kanban' ? 'block' : 'none';
        
        this.renderTasks();
    }

    filterTasks() {
        this.pagination.currentPage = 1; // Reset to first page when filtering
        this.renderTasks();
    }

    searchTasks() {
        this.filterTasks();
    }

    updatePaginationControls(totalTasks) {
        const pagination = document.getElementById('pagination');
        const pageNumbers = document.getElementById('pageNumbers');
        const pageInfo = document.getElementById('pageInfo');
        const prevButton = document.getElementById('prevPage');
        const nextButton = document.getElementById('nextPage');
        
        if (!pagination || !pageNumbers || !pageInfo || !prevButton || !nextButton) return;
        
        // Show pagination if there are multiple pages
        if (this.pagination.totalPages > 1) {
            pagination.style.display = 'flex';
        } else {
            pagination.style.display = 'none';
            return;
        }
        
        // Update page info
        const startItem = (this.pagination.currentPage - 1) * this.pagination.itemsPerPage + 1;
        const endItem = Math.min(this.pagination.currentPage * this.pagination.itemsPerPage, totalTasks);
        pageInfo.textContent = `Page ${this.pagination.currentPage} of ${this.pagination.totalPages} (${startItem}-${endItem} of ${totalTasks})`;
        
        // Update prev/next buttons
        prevButton.disabled = this.pagination.currentPage === 1;
        nextButton.disabled = this.pagination.currentPage === this.pagination.totalPages;
        
        // Generate page numbers
        let pageNumbersHtml = '';
        const maxVisiblePages = 5;
        let startPage = Math.max(1, this.pagination.currentPage - Math.floor(maxVisiblePages / 2));
        let endPage = Math.min(this.pagination.totalPages, startPage + maxVisiblePages - 1);
        
        if (endPage - startPage + 1 < maxVisiblePages) {
            startPage = Math.max(1, endPage - maxVisiblePages + 1);
        }
        
        for (let i = startPage; i <= endPage; i++) {
            const isActive = i === this.pagination.currentPage;
            pageNumbersHtml += `<span class="page-number ${isActive ? 'active' : ''}" onclick="taskManager.goToPage(${i})">${i}</span>`;
        }
        
        pageNumbers.innerHTML = pageNumbersHtml;
    }

    goToPage(page) {
        if (page >= 1 && page <= this.pagination.totalPages) {
            this.pagination.currentPage = page;
            this.renderTasks();
        }
    }

    changePage(direction) {
        const newPage = this.pagination.currentPage + direction;
        this.goToPage(newPage);
    }

    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    getCurrentUser() {
        // Try to get user name from localStorage (from settings)
        const agentName = localStorage.getItem('agentName');
        if (agentName && agentName !== 'Cuttle') {
            return agentName;
        }
        
        // Fallback to a default user name or prompt for one
        const userName = localStorage.getItem('userName') || 'Anonymous User';
        
        // If no user name is set, prompt for one (only once per session)
        if (!localStorage.getItem('userName') && !localStorage.getItem('userNamePrompted')) {
            const name = prompt('Please enter your name for comments:');
            if (name && name.trim()) {
                localStorage.setItem('userName', name.trim());
                localStorage.setItem('userNamePrompted', 'true');
                return name.trim();
            } else {
                localStorage.setItem('userNamePrompted', 'true');
            }
        }
        
        return userName;
    }

    showCommentSuccessIndicator(message = 'Comment added') {
        // Create a temporary success indicator
        const indicator = document.createElement('div');
        indicator.className = 'comment-success-indicator';
        indicator.innerHTML = `✅ ${message}`;
        indicator.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #4CAF50;
            color: white;
            padding: 10px 15px;
            border-radius: 5px;
            font-size: 14px;
            z-index: 10000;
            animation: slideInRight 0.3s ease;
        `;
        
        document.body.appendChild(indicator);
        
        // Remove after 2 seconds
        setTimeout(() => {
            indicator.style.animation = 'slideOutRight 0.3s ease';
            setTimeout(() => {
                if (indicator.parentNode) {
                    indicator.parentNode.removeChild(indicator);
                }
            }, 300);
        }, 2000);
        
        // Add CSS animations if not already present
        if (!document.getElementById('commentSuccessStyles')) {
            const style = document.createElement('style');
            style.id = 'commentSuccessStyles';
            style.textContent = `
                @keyframes slideInRight {
                    from { transform: translateX(100%); opacity: 0; }
                    to { transform: translateX(0); opacity: 1; }
                }
                @keyframes slideOutRight {
                    from { transform: translateX(0); opacity: 1; }
                    to { transform: translateX(100%); opacity: 0; }
                }
            `;
            document.head.appendChild(style);
        }
    }

    showCommentErrorIndicator(message = 'Failed to save comment') {
        // Create a temporary error indicator
        const indicator = document.createElement('div');
        indicator.className = 'comment-error-indicator';
        indicator.innerHTML = `❌ ${message}`;
        indicator.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            background: #f44336;
            color: white;
            padding: 10px 15px;
            border-radius: 5px;
            font-size: 14px;
            z-index: 10000;
            animation: slideInRight 0.3s ease;
        `;
        
        document.body.appendChild(indicator);
        
        // Remove after 3 seconds
        setTimeout(() => {
            indicator.style.animation = 'slideOutRight 0.3s ease';
            setTimeout(() => {
                if (indicator.parentNode) {
                    indicator.parentNode.removeChild(indicator);
                }
            }, 300);
        }, 3000);
    }

    updatePriorityFilter() {
        console.log('updatePriorityFilter called');
        // Always use portal checkboxes if they exist, otherwise use original
        const originalCheckboxes = document.querySelectorAll('#priorityFilterOptions input[type="checkbox"]');
        const portalCheckboxes = document.querySelectorAll('#priorityFilterPortal input[type="checkbox"]');
        const checkboxes = portalCheckboxes.length > 0 ? portalCheckboxes : originalCheckboxes;
        
        console.log('Found checkboxes:', { original: originalCheckboxes.length, portal: portalCheckboxes.length });
        console.log('Using checkboxes:', checkboxes.length);
        
        this.filters.priorities = Array.from(checkboxes)
            .filter(checkbox => checkbox.checked)
            .map(checkbox => checkbox.value);
        
        console.log('Selected priorities:', this.filters.priorities);
        
        // Sync checkboxes between portal and original (reverse sync)
        if (originalCheckboxes.length > 0 && portalCheckboxes.length > 0) {
            portalCheckboxes.forEach((portal, index) => {
                const original = originalCheckboxes[index];
                if (original) {
                    original.checked = portal.checked;
                }
            });
        }
        
        this.updatePriorityFilterText();
        this.filterTasks();
    }

    updatePriorityFilterText() {
        const textElement = document.getElementById('priorityFilterText');
        if (!textElement) return;
        
        if (this.filters.priorities.length === 0) {
            textElement.textContent = 'All Priorities';
        } else if (this.filters.priorities.length === 1) {
            textElement.textContent = this.filters.priorities[0];
        } else {
            textElement.textContent = `${this.filters.priorities.length} Selected`;
        }
    }

    togglePriorityDropdown() {
        console.log('togglePriorityDropdown called');
        const dropdown = document.getElementById('priorityFilter');
        const trigger = dropdown?.querySelector('.multiselect-trigger');
        
        if (dropdown && trigger) {
            const existingPortal = document.getElementById('priorityFilterPortal');
            
            if (existingPortal) {
                // Close dropdown
                existingPortal.remove();
                dropdown.classList.remove('active');
                console.log('Dropdown closed');
            } else {
                // Open dropdown
                this.createDropdownPortal(trigger);
                dropdown.classList.add('active');
                console.log('Dropdown opened');
            }
        } else {
            console.error('Missing elements:', { dropdown, trigger });
        }
    }

    createDropdownPortal(trigger) {
        // Create portal dropdown
        const portal = document.createElement('div');
        portal.id = 'priorityFilterPortal';
        portal.className = 'multiselect-options active';
        
        // Get the original options content
        const originalOptions = document.getElementById('priorityFilterOptions');
        if (originalOptions) {
            portal.innerHTML = originalOptions.innerHTML;
        }
        
        // Position the portal
        const rect = trigger.getBoundingClientRect();
        portal.style.position = 'fixed';
        portal.style.top = (rect.bottom + 4) + 'px';
        portal.style.left = rect.left + 'px';
        portal.style.width = Math.max(rect.width, 160) + 'px';
        portal.style.zIndex = '10000';
        
        // Add click handlers to checkboxes and labels
        const checkboxes = portal.querySelectorAll('input[type="checkbox"]');
        const labels = portal.querySelectorAll('label.multiselect-option');
        console.log('Priority checkboxes found:', checkboxes.length);
        
        checkboxes.forEach((checkbox, index) => {
            console.log(`Setting up checkbox ${index}:`, checkbox.value);
            checkbox.addEventListener('change', (e) => {
                console.log('Priority checkbox changed:', e.target.value, e.target.checked);
                // Use setTimeout to ensure the checkbox state has been updated
                setTimeout(() => {
                    this.updatePriorityFilter();
                }, 0);
            });
        });
        
        // Add click handlers to labels to ensure clicking anywhere on the label toggles the checkbox
        labels.forEach((label, index) => {
            const checkbox = label.querySelector('input[type="checkbox"]');
            if (checkbox) {
                label.addEventListener('click', (e) => {
                    // Only handle if the click wasn't directly on the checkbox
                    if (e.target !== checkbox) {
                        e.preventDefault();
                        checkbox.checked = !checkbox.checked;
                        // Trigger change event to update filters
                        checkbox.dispatchEvent(new Event('change'));
                    }
                });
            }
        });
        
        // Add click outside handler
        const clickOutsideHandler = (e) => {
            // Don't close if clicking on checkboxes, labels, or any child elements within the portal
            if (e.target.type === 'checkbox' || 
                e.target.tagName === 'LABEL' || 
                portal.contains(e.target)) {
                return;
            }
            
            if (!trigger.contains(e.target)) {
                portal.remove();
                document.getElementById('priorityFilter').classList.remove('active');
                document.removeEventListener('click', clickOutsideHandler);
            }
        };
        
        // Add to body and set up event listener
        document.body.appendChild(portal);
        setTimeout(() => {
            document.addEventListener('click', clickOutsideHandler);
        }, 0);
        
        console.log('Portal created and positioned at:', {
            top: portal.style.top,
            left: portal.style.left,
            width: portal.style.width
        });
    }

    positionDropdown(trigger, options) {
        const rect = trigger.getBoundingClientRect();
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
        
        console.log('Positioning dropdown:', {
            rect,
            scrollTop,
            scrollLeft,
            calculatedTop: rect.bottom + scrollTop + 4,
            calculatedLeft: rect.left + scrollLeft
        });
        
        // Position the dropdown below the trigger
        options.style.top = (rect.bottom + scrollTop + 4) + 'px';
        options.style.left = (rect.left + scrollLeft) + 'px';
        options.style.width = Math.max(rect.width, 160) + 'px';
        
        console.log('Applied styles:', {
            top: options.style.top,
            left: options.style.left,
            width: options.style.width
        });
    }

    closePriorityDropdown() {
        const portal = document.getElementById('priorityFilterPortal');
        const dropdown = document.getElementById('priorityFilter');
        
        if (portal) {
            portal.remove();
        }
        if (dropdown) {
            dropdown.classList.remove('active');
        }
    }

    // Status filter functions
    updateStatusFilter() {
        console.log('updateStatusFilter called');
        // Always use portal checkboxes if they exist, otherwise use original
        const originalCheckboxes = document.querySelectorAll('#statusFilterOptions input[type="checkbox"]');
        const portalCheckboxes = document.querySelectorAll('#statusFilterPortal input[type="checkbox"]');
        const checkboxes = portalCheckboxes.length > 0 ? portalCheckboxes : originalCheckboxes;
        
        console.log('Found status checkboxes:', { original: originalCheckboxes.length, portal: portalCheckboxes.length });
        
        this.filters.statuses = Array.from(checkboxes)
            .filter(checkbox => checkbox.checked)
            .map(checkbox => checkbox.value);
        
        console.log('Selected statuses:', this.filters.statuses);
        
        // Sync checkboxes between portal and original (reverse sync)
        if (originalCheckboxes.length > 0 && portalCheckboxes.length > 0) {
            portalCheckboxes.forEach((portal, index) => {
                const original = originalCheckboxes[index];
                if (original) {
                    original.checked = portal.checked;
                }
            });
        }
        
        this.updateStatusFilterText();
        this.filterTasks();
    }

    updateStatusFilterText() {
        const textElement = document.getElementById('statusFilterText');
        if (!textElement) return;
        
        if (this.filters.statuses.length === 0) {
            textElement.textContent = 'All Status';
        } else if (this.filters.statuses.length === 1) {
            textElement.textContent = this.filters.statuses[0];
        } else if (this.filters.statuses.length === 4 && 
                   this.filters.statuses.includes('None') && 
                   this.filters.statuses.includes('Planned') && 
                   this.filters.statuses.includes('In Progress') && 
                   this.filters.statuses.includes('Blocked')) {
            // Special case: if all non-closed statuses are selected, show "Active Tasks"
            textElement.textContent = 'Active Tasks';
        } else {
            textElement.textContent = `${this.filters.statuses.length} Selected`;
        }
    }

    toggleStatusDropdown() {
        const dropdown = document.getElementById('statusFilter');
        const trigger = dropdown?.querySelector('.multiselect-trigger');
        
        if (dropdown && trigger) {
            const existingPortal = document.getElementById('statusFilterPortal');
            
            if (existingPortal) {
                existingPortal.remove();
                dropdown.classList.remove('active');
            } else {
                this.createStatusDropdownPortal(trigger);
                dropdown.classList.add('active');
            }
        }
    }

    createStatusDropdownPortal(trigger) {
        const portal = document.createElement('div');
        portal.id = 'statusFilterPortal';
        portal.className = 'multiselect-options active';
        
        const originalOptions = document.getElementById('statusFilterOptions');
        if (originalOptions) {
            portal.innerHTML = originalOptions.innerHTML;
        }
        
        const rect = trigger.getBoundingClientRect();
        portal.style.position = 'fixed';
        portal.style.top = (rect.bottom + 4) + 'px';
        portal.style.left = rect.left + 'px';
        portal.style.width = Math.max(rect.width, 160) + 'px';
        portal.style.zIndex = '10000';
        
        const checkboxes = portal.querySelectorAll('input[type="checkbox"]');
        const labels = portal.querySelectorAll('label.multiselect-option');
        console.log('Status checkboxes found:', checkboxes.length);
        
        checkboxes.forEach((checkbox, index) => {
            console.log(`Setting up status checkbox ${index}:`, checkbox.value);
            checkbox.addEventListener('change', (e) => {
                console.log('Status checkbox changed:', e.target.value, e.target.checked);
                // Use setTimeout to ensure the checkbox state has been updated
                setTimeout(() => {
                    this.updateStatusFilter();
                }, 0);
            });
        });
        
        // Add click handlers to labels to ensure clicking anywhere on the label toggles the checkbox
        labels.forEach((label, index) => {
            const checkbox = label.querySelector('input[type="checkbox"]');
            if (checkbox) {
                label.addEventListener('click', (e) => {
                    // Only handle if the click wasn't directly on the checkbox
                    if (e.target !== checkbox) {
                        e.preventDefault();
                        checkbox.checked = !checkbox.checked;
                        // Trigger change event to update filters
                        checkbox.dispatchEvent(new Event('change'));
                    }
                });
            }
        });
        
        const clickOutsideHandler = (e) => {
            // Don't close if clicking on checkboxes, labels, or any child elements within the portal
            if (e.target.type === 'checkbox' || 
                e.target.tagName === 'LABEL' || 
                portal.contains(e.target)) {
                return;
            }
            
            if (!trigger.contains(e.target)) {
                portal.remove();
                document.getElementById('statusFilter').classList.remove('active');
                document.removeEventListener('click', clickOutsideHandler);
            }
        };
        
        document.body.appendChild(portal);
        setTimeout(() => {
            document.addEventListener('click', clickOutsideHandler);
        }, 0);
    }

    // Owner filter functions
    updateOwnerFilter() {
        console.log('updateOwnerFilter called');
        // Always use portal checkboxes if they exist, otherwise use original
        const originalCheckboxes = document.querySelectorAll('#ownerFilterOptions input[type="checkbox"]');
        const portalCheckboxes = document.querySelectorAll('#ownerFilterPortal input[type="checkbox"]');
        const checkboxes = portalCheckboxes.length > 0 ? portalCheckboxes : originalCheckboxes;
        
        console.log('Found owner checkboxes:', { original: originalCheckboxes.length, portal: portalCheckboxes.length });
        
        this.filters.owners = Array.from(checkboxes)
            .filter(checkbox => checkbox.checked)
            .map(checkbox => checkbox.value);
        
        console.log('Selected owners:', this.filters.owners);
        
        // Sync checkboxes between portal and original (reverse sync)
        if (originalCheckboxes.length > 0 && portalCheckboxes.length > 0) {
            portalCheckboxes.forEach((portal, index) => {
                const original = originalCheckboxes[index];
                if (original) {
                    original.checked = portal.checked;
                }
            });
        }
        
        this.updateOwnerFilterText();
        this.filterTasks();
    }

    updateOwnerFilterText() {
        const textElement = document.getElementById('ownerFilterText');
        if (!textElement) return;
        
        if (this.filters.owners.length === 0) {
            textElement.textContent = 'All Owners';
        } else if (this.filters.owners.length === 1) {
            textElement.textContent = this.filters.owners[0];
        } else {
            textElement.textContent = `${this.filters.owners.length} Selected`;
        }
    }

    toggleOwnerDropdown() {
        const dropdown = document.getElementById('ownerFilter');
        const trigger = dropdown?.querySelector('.multiselect-trigger');
        
        if (dropdown && trigger) {
            const existingPortal = document.getElementById('ownerFilterPortal');
            
            if (existingPortal) {
                existingPortal.remove();
                dropdown.classList.remove('active');
            } else {
                this.createOwnerDropdownPortal(trigger);
                dropdown.classList.add('active');
            }
        }
    }

    createOwnerDropdownPortal(trigger) {
        const portal = document.createElement('div');
        portal.id = 'ownerFilterPortal';
        portal.className = 'multiselect-options active';
        
        const originalOptions = document.getElementById('ownerFilterOptions');
        if (originalOptions) {
            portal.innerHTML = originalOptions.innerHTML;
        }
        
        const rect = trigger.getBoundingClientRect();
        portal.style.position = 'fixed';
        portal.style.top = (rect.bottom + 4) + 'px';
        portal.style.left = rect.left + 'px';
        portal.style.width = Math.max(rect.width, 160) + 'px';
        portal.style.zIndex = '10000';
        
        const checkboxes = portal.querySelectorAll('input[type="checkbox"]');
        const labels = portal.querySelectorAll('label.multiselect-option');
        console.log('Owner checkboxes found:', checkboxes.length);
        
        checkboxes.forEach((checkbox, index) => {
            console.log(`Setting up owner checkbox ${index}:`, checkbox.value);
            checkbox.addEventListener('change', (e) => {
                console.log('Owner checkbox changed:', e.target.value, e.target.checked);
                // Use setTimeout to ensure the checkbox state has been updated
                setTimeout(() => {
                    this.updateOwnerFilter();
                }, 0);
            });
        });
        
        // Add click handlers to labels to ensure clicking anywhere on the label toggles the checkbox
        labels.forEach((label, index) => {
            const checkbox = label.querySelector('input[type="checkbox"]');
            if (checkbox) {
                label.addEventListener('click', (e) => {
                    // Only handle if the click wasn't directly on the checkbox
                    if (e.target !== checkbox) {
                        e.preventDefault();
                        checkbox.checked = !checkbox.checked;
                        // Trigger change event to update filters
                        checkbox.dispatchEvent(new Event('change'));
                    }
                });
            }
        });
        
        const clickOutsideHandler = (e) => {
            // Don't close if clicking on checkboxes, labels, or any child elements within the portal
            if (e.target.type === 'checkbox' || 
                e.target.tagName === 'LABEL' || 
                portal.contains(e.target)) {
                return;
            }
            
            if (!trigger.contains(e.target)) {
                portal.remove();
                document.getElementById('ownerFilter').classList.remove('active');
                document.removeEventListener('click', clickOutsideHandler);
            }
        };
        
        document.body.appendChild(portal);
        setTimeout(() => {
            document.addEventListener('click', clickOutsideHandler);
        }, 0);
    }

    changeSorting() {
        const sortSelect = document.getElementById('sortSelect');
        if (!sortSelect) return;
        
        const value = sortSelect.value;
        const [field, direction] = value.split('-');
        
        this.sorting.field = field;
        this.sorting.direction = direction;
        
        console.log('Sorting changed:', this.sorting);
        
        // Re-render tasks with new sorting
        this.renderTasks();
    }
}

// Global functions for HTML onclick handlers
let taskManager;

function showCreateTaskModal() {
    taskManager.showCreateTaskModal();
}

function showTaskDetail(taskId) {
    taskManager.showTaskDetail(taskId);
}

function saveTask() {
    taskManager.saveTask();
}

function deleteCurrentTask() {
    taskManager.deleteCurrentTask();
}

function editCurrentTask() {
    taskManager.editCurrentTask();
}

function addComment() {
    taskManager.addComment();
}

function closeTaskViaCommit() {
    taskManager.showCommitCloseModal();
}

function executeCommitClose() {
    taskManager.executeCommitClose();
}

function closeTaskModal() {
    taskManager.closeTaskModal();
}

function closeTaskDetailModal() {
    taskManager.closeTaskDetailModal();
}

function closeCommitModal() {
    taskManager.closeCommitModal();
}

function toggleView(view) {
    taskManager.toggleView(view);
}

function filterTasks() {
    taskManager.filterTasks();
}

function searchTasks() {
    taskManager.searchTasks();
}

function changePage(direction) {
    taskManager.changePage(direction);
}

function togglePriorityDropdown() {
    taskManager.togglePriorityDropdown();
}

function updatePriorityFilter() {
    taskManager.updatePriorityFilter();
}

function toggleStatusDropdown() {
    taskManager.toggleStatusDropdown();
}

function updateStatusFilter() {
    taskManager.updateStatusFilter();
}

function toggleOwnerDropdown() {
    taskManager.toggleOwnerDropdown();
}

function updateOwnerFilter() {
    taskManager.updateOwnerFilter();
}

function changeSorting() {
    taskManager.changeSorting();
}

// Initialize when DOM is loaded
document.addEventListener('DOMContentLoaded', () => {
    taskManager = new TaskManager();
    
    // Setup commit preview updates
    const commitMessage = document.getElementById('commitMessage');
    const commitFiles = document.getElementById('commitFiles');
    
    if (commitMessage) {
        commitMessage.addEventListener('input', () => taskManager.updateCommitPreview());
    }
    
    if (commitFiles) {
        commitFiles.addEventListener('input', () => taskManager.updateCommitPreview());
    }
});
