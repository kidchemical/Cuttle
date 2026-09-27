// Git Web UI JavaScript for Cuttle

// Global variables
let gitData = {
    status: null,
    branches: [],
    commits: [],
    files: [],
    currentBranch: null
};

// Pagination state
let paginationState = {
    files: { page: 1, per_page: 50, total: 0, pages: 0 },
    commits: { page: 1, per_page: 20, total: 0, pages: 0 },
    diff: { page: 1, per_page: 100, total_lines: 0, pages: 0 }
};

let projectData = {
    projects: [],
    currentProject: null
};

// Initialize Git UI when DOM is loaded
document.addEventListener('DOMContentLoaded', function() {
    initializeGitUI();
});

// Initialize Git UI
function initializeGitUI() {
    console.log('Initializing Git UI...');
    
    // Load project data first, then Git data
    loadProjectData().then(() => {
        loadGitData();
    });
    
    // Set up auto-refresh every 30 seconds
    setInterval(() => {
        loadProjectData();
        loadGitData();
    }, 30000);
}

// Load all Git data
async function loadGitData() {
    try {
        await Promise.all([
            loadGitStatus(),
            loadGitBranches(),
            loadGitCommits(),
            loadGitFiles()
        ]);
    } catch (error) {
        console.error('Error loading Git data:', error);
        showError('Failed to load Git data: ' + error.message);
    }
}

// Load Git status
async function loadGitStatus() {
    try {
        const response = await fetch('/api/git/status');
        const data = await response.json();
        
        if (data.success) {
            gitData.status = data.data;
            updateGitStatusDisplay();
        } else {
            throw new Error(data.error || 'Failed to get Git status');
        }
    } catch (error) {
        console.error('Error loading Git status:', error);
        document.getElementById('gitStatusContent').innerHTML = `
            <div class="git-error">
                ❌ Failed to load Git status: ${error.message}
            </div>
        `;
    }
}

// Load Git branches
async function loadGitBranches() {
    try {
        const response = await fetch('/api/git/branches');
        const data = await response.json();
        
        if (data.success) {
            gitData.branches = data.data;
            gitData.currentBranch = data.current;
            updateGitBranchesDisplay();
            updateBranchSelect();
        } else {
            throw new Error(data.error || 'Failed to get Git branches');
        }
    } catch (error) {
        console.error('Error loading Git branches:', error);
        document.getElementById('gitBranchContent').innerHTML = `
            <div class="git-error">
                ❌ Failed to load Git branches: ${error.message}
            </div>
        `;
    }
}

// Load Git commits
async function loadGitCommits(page = 1) {
    try {
        const response = await fetch(`/api/git/commits?page=${page}&per_page=${paginationState.commits.per_page}`);
        const data = await response.json();
        
        if (data.success) {
            gitData.commits = data.data;
            paginationState.commits = data.pagination;
            updateGitCommitsDisplay();
        } else {
            throw new Error(data.error || 'Failed to get Git commits');
        }
    } catch (error) {
        console.error('Error loading Git commits:', error);
        document.getElementById('gitCommitsContent').innerHTML = `
            <div class="git-error">
                ❌ Failed to load Git commits: ${error.message}
            </div>
        `;
    }
}

// Load Git files
async function loadGitFiles(page = 1, statusFilter = null) {
    try {
        let url = `/api/git/files?page=${page}&per_page=${paginationState.files.per_page}`;
        if (statusFilter) {
            url += `&status=${statusFilter}`;
        }
        
        const response = await fetch(url);
        const data = await response.json();
        
        if (data.success) {
            gitData.files = data.data;
            paginationState.files = data.pagination;
            updateGitFilesDisplay();
            updateCommitFilesList();
        } else {
            throw new Error(data.error || 'Failed to get Git files');
        }
    } catch (error) {
        console.error('Error loading Git files:', error);
        document.getElementById('gitFilesContent').innerHTML = `
            <div class="git-error">
                ❌ Failed to load Git files: ${error.message}
            </div>
        `;
    }
}

// Update Git status display
function updateGitStatusDisplay() {
    const content = document.getElementById('gitStatusContent');
    if (!gitData.status) return;
    
    const status = gitData.status;
    let html = '';
    
    // Repository status
    html += `
        <div class="git-status">
            <span class="git-status-icon">📁</span>
            <span class="git-status-text">Repository: ${status.repository || 'Unknown'}</span>
        </div>
    `;
    
    // Current branch
    if (status.currentBranch) {
        html += `
            <div class="git-status">
                <span class="git-status-icon">🌿</span>
                <span class="git-status-text">Current Branch: ${status.currentBranch}</span>
            </div>
        `;
    }
    
    // Working directory status
    if (status.workingDirectory) {
        const wd = status.workingDirectory;
        html += `
            <div class="git-status">
                <span class="git-status-icon">📂</span>
                <span class="git-status-text">Working Directory: ${wd.clean ? 'Clean' : 'Modified'}</span>
            </div>
        `;
        
        if (!wd.clean) {
            if (wd.modified > 0) {
                html += `
                    <div class="git-status">
                        <span class="git-status-icon">📝</span>
                        <span class="git-status-text">Modified: ${wd.modified} files</span>
                    </div>
                `;
            }
            if (wd.added > 0) {
                html += `
                    <div class="git-status">
                        <span class="git-status-icon">➕</span>
                        <span class="git-status-text">Added: ${wd.added} files</span>
                    </div>
                `;
            }
            if (wd.deleted > 0) {
                html += `
                    <div class="git-status">
                        <span class="git-status-icon">🗑️</span>
                        <span class="git-status-text">Deleted: ${wd.deleted} files</span>
                    </div>
                `;
            }
            if (wd.untracked > 0) {
                html += `
                    <div class="git-status">
                        <span class="git-status-icon">❓</span>
                        <span class="git-status-text">Untracked: ${wd.untracked} files</span>
                    </div>
                `;
            }
        }
    }
    
    // Staging area
    if (status.stagingArea) {
        const staging = status.stagingArea;
        html += `
            <div class="git-status">
                <span class="git-status-icon">🎯</span>
                <span class="git-status-text">Staging Area: ${staging.staged} files staged</span>
            </div>
        `;
    }
    
    content.innerHTML = html;
}

// Update Git branches display
function updateGitBranchesDisplay() {
    const content = document.getElementById('gitBranchContent');
    if (!gitData.branches || gitData.branches.length === 0) {
        content.innerHTML = '<div class="git-warning">No branches found</div>';
        return;
    }
    
    let html = '<ul class="git-branch-list">';
    
    gitData.branches.forEach(branch => {
        const isCurrent = branch.name === gitData.currentBranch;
        html += `
            <li class="git-branch-item ${isCurrent ? 'current' : ''}">
                <span class="git-branch-name">${branch.name}</span>
                <div class="git-branch-actions">
                    ${!isCurrent ? `
                        <button class="git-branch-btn git-btn-primary" onclick="switchToBranch('${branch.name}')">
                            Switch
                        </button>
                    ` : ''}
                    <button class="git-branch-btn git-btn-danger" onclick="deleteBranch('${branch.name}')" ${isCurrent ? 'disabled' : ''}>
                        Delete
                    </button>
                </div>
            </li>
        `;
    });
    
    html += '</ul>';
    content.innerHTML = html;
}

// Update Git commits display
function updateGitCommitsDisplay() {
    const content = document.getElementById('gitCommitsContent');
    if (!gitData.commits || gitData.commits.length === 0) {
        content.innerHTML = '<div class="git-warning">No commits found</div>';
        return;
    }
    
    let html = '<ul class="git-commit-list">';
    
    gitData.commits.forEach(commit => {
        const date = new Date(commit.date).toLocaleString();
        html += `
            <li class="git-commit-item" onclick="showCommitDiff('${commit.hash}')" style="cursor: pointer;">
                <div class="git-commit-header">
                    <span class="git-commit-hash">${commit.hash.substring(0, 8)}</span>
                    <span class="git-commit-date">${date}</span>
                </div>
                <div class="git-commit-message">${escapeHtml(commit.message)}</div>
                <div class="git-commit-author">by ${commit.author}</div>
            </li>
        `;
    });
    
    html += '</ul>';
    
    // Add pagination controls
    html += createPaginationControls('commits', paginationState.commits);
    
    content.innerHTML = html;
}

// Update Git files display
function updateGitFilesDisplay() {
    const content = document.getElementById('gitFilesContent');
    if (!gitData.files || gitData.files.length === 0) {
        content.innerHTML = '<div class="git-warning">No files found</div>';
        return;
    }
    
    let html = '';
    
    // Add filter controls
    html += `
        <div class="git-filter-controls" style="margin-bottom: 15px;">
            <select id="fileStatusFilter" onchange="filterFilesByStatus()" style="padding: 5px; border: 1px solid var(--border-color); border-radius: 4px; background: var(--bg-secondary); color: var(--text-primary);">
                <option value="">All Files</option>
                <option value="modified">Modified</option>
                <option value="added">Added</option>
                <option value="deleted">Deleted</option>
                <option value="untracked">Untracked</option>
                <option value="clean">Clean</option>
            </select>
        </div>
    `;
    
    html += '<ul class="git-file-list">';
    
    gitData.files.forEach(file => {
        html += `
            <li class="git-file-item">
                <span class="git-file-name">${file.name}</span>
                <span class="git-file-status ${file.status}">${file.status}</span>
            </li>
        `;
    });
    
    html += '</ul>';
    
    // Add pagination controls
    html += createPaginationControls('files', paginationState.files);
    
    content.innerHTML = html;
}

// Update branch select dropdown
function updateBranchSelect() {
    const select = document.getElementById('branchSelect');
    if (!select || !gitData.branches) return;
    
    select.innerHTML = '';
    gitData.branches.forEach(branch => {
        const option = document.createElement('option');
        option.value = branch.name;
        option.textContent = branch.name;
        if (branch.name === gitData.currentBranch) {
            option.selected = true;
        }
        select.appendChild(option);
    });
}

// Update commit files list
function updateCommitFilesList() {
    const container = document.getElementById('commitFilesList');
    if (!container || !gitData.files) return;
    
    let html = '';
    gitData.files.forEach(file => {
        if (file.status !== 'clean') {
            html += `
                <label class="git-file-toggle-wrapper">
                    <input type="checkbox" value="${file.name}" class="git-file-toggle-input" checked>
                    <span class="git-file-toggle-track"><span class="git-file-toggle-thumb"></span></span>
                    <span class="git-file-name">${file.name}</span>
                    <span class="git-file-status ${file.status}" style="margin-left: auto;">${file.status}</span>
                </label>
            `;
        }
    });
    
    container.innerHTML = html || '<div class="git-warning">No files to commit</div>';
}

// Modal functions
function showModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.add('active');
        document.body.style.overflow = 'hidden';
    }
}

function hideModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.classList.remove('active');
        document.body.style.overflow = '';
    }
}

// Git operations
async function performCommit() {
    const message = document.getElementById('commitMessage').value.trim();
    if (!message) {
        showError('Please enter a commit message');
        return;
    }
    
    const checkboxes = document.querySelectorAll('#commitFilesList input[type="checkbox"]:checked');
    const files = Array.from(checkboxes).map(cb => cb.value);
    
    if (files.length === 0) {
        showError('Please select at least one file to commit');
        return;
    }
    
    try {
        showLoading('commitModal');
        
        const response = await fetch('/api/git/commit', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                message: message,
                files: files
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSuccess('Commit successful!');
            hideModal('commitModal');
            document.getElementById('commitMessage').value = '';
            loadGitData();
        } else {
            throw new Error(data.error || 'Commit failed');
        }
    } catch (error) {
        console.error('Commit error:', error);
        showError('Commit failed: ' + error.message);
    } finally {
        hideLoading('commitModal');
    }
}

async function performPull() {
    const remote = document.getElementById('pullRemote').value;
    const branch = document.getElementById('pullBranch').value;
    
    try {
        showLoading('pullModal');
        
        const response = await fetch('/api/git/pull', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                remote: remote,
                branch: branch
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSuccess('Pull successful!');
            hideModal('pullModal');
            loadGitData();
        } else {
            throw new Error(data.error || 'Pull failed');
        }
    } catch (error) {
        console.error('Pull error:', error);
        showError('Pull failed: ' + error.message);
    } finally {
        hideLoading('pullModal');
    }
}

async function performPush() {
    const remote = document.getElementById('pushRemote').value;
    const branch = document.getElementById('pushBranch').value;
    
    try {
        showLoading('pushModal');
        
        const response = await fetch('/api/git/push', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                remote: remote,
                branch: branch
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSuccess('Push successful!');
            hideModal('pushModal');
            loadGitData();
        } else {
            throw new Error(data.error || 'Push failed');
        }
    } catch (error) {
        console.error('Push error:', error);
        showError('Push failed: ' + error.message);
    } finally {
        hideLoading('pushModal');
    }
}

async function switchBranch() {
    const branchSelect = document.getElementById('branchSelect');
    const newBranchName = document.getElementById('newBranchName').value.trim();
    
    let branchName;
    if (newBranchName) {
        branchName = newBranchName;
    } else {
        branchName = branchSelect.value;
    }
    
    if (!branchName) {
        showError('Please select a branch or enter a new branch name');
        return;
    }
    
    try {
        showLoading('branchModal');
        
        const response = await fetch('/api/git/branch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                action: newBranchName ? 'create' : 'switch',
                branch: branchName
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSuccess(`Successfully ${newBranchName ? 'created and switched to' : 'switched to'} branch: ${branchName}`);
            hideModal('branchModal');
            document.getElementById('newBranchName').value = '';
            loadGitData();
        } else {
            throw new Error(data.error || 'Branch operation failed');
        }
    } catch (error) {
        console.error('Branch operation error:', error);
        showError('Branch operation failed: ' + error.message);
    } finally {
        hideLoading('branchModal');
    }
}

async function switchToBranch(branchName) {
    try {
        const response = await fetch('/api/git/branch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                action: 'switch',
                branch: branchName
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSuccess(`Switched to branch: ${branchName}`);
            loadGitData();
        } else {
            throw new Error(data.error || 'Branch switch failed');
        }
    } catch (error) {
        console.error('Branch switch error:', error);
        showError('Branch switch failed: ' + error.message);
    }
}

async function deleteBranch(branchName) {
    if (!confirm(`Are you sure you want to delete branch "${branchName}"?`)) {
        return;
    }
    
    try {
        const response = await fetch('/api/git/branch', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                action: 'delete',
                branch: branchName
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showSuccess(`Deleted branch: ${branchName}`);
            loadGitData();
        } else {
            throw new Error(data.error || 'Branch deletion failed');
        }
    } catch (error) {
        console.error('Branch deletion error:', error);
        showError('Branch deletion failed: ' + error.message);
    }
}

// Utility functions
function refreshGitStatus() {
    loadGitData();
}

function showLoading(modalId) {
    const modal = document.getElementById(modalId);
    const footer = modal.querySelector('.git-modal-footer');
    if (footer) {
        footer.innerHTML = '<div class="git-loading"><div class="git-spinner"></div>Processing...</div>';
    }
}

function hideLoading(modalId) {
    // Restore original footer content
    const modal = document.getElementById(modalId);
    if (modal) {
        // This would need to be implemented based on the specific modal
        // For now, we'll just remove the loading state
        const footer = modal.querySelector('.git-modal-footer');
        if (footer && footer.innerHTML.includes('git-loading')) {
            // Restore original buttons - this is a simplified approach
            location.reload();
        }
    }
}

function showError(message) {
    // Create a temporary error message
    const errorDiv = document.createElement('div');
    errorDiv.className = 'git-error';
    errorDiv.textContent = '❌ ' + message;
    errorDiv.style.position = 'fixed';
    errorDiv.style.top = '20px';
    errorDiv.style.right = '20px';
    errorDiv.style.zIndex = '10000';
    errorDiv.style.maxWidth = '400px';
    
    document.body.appendChild(errorDiv);
    
    setTimeout(() => {
        if (errorDiv.parentNode) {
            errorDiv.parentNode.removeChild(errorDiv);
        }
    }, 5000);
}

function showSuccess(message) {
    // Create a temporary success message
    const successDiv = document.createElement('div');
    successDiv.className = 'git-success';
    successDiv.textContent = '✅ ' + message;
    successDiv.style.position = 'fixed';
    successDiv.style.top = '20px';
    successDiv.style.right = '20px';
    successDiv.style.zIndex = '10000';
    successDiv.style.maxWidth = '400px';
    
    document.body.appendChild(successDiv);
    
    setTimeout(() => {
        if (successDiv.parentNode) {
            successDiv.parentNode.removeChild(successDiv);
        }
    }, 3000);
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Close modals when clicking outside
document.addEventListener('click', function(event) {
    if (event.target.classList.contains('git-modal')) {
        event.target.classList.remove('active');
        document.body.style.overflow = '';
    }
});

// Close modals with Escape key
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
        const activeModal = document.querySelector('.git-modal.active');
        if (activeModal) {
            activeModal.classList.remove('active');
            document.body.style.overflow = '';
        }
    }
});

// Project Management Functions
async function loadProjectData() {
    try {
        const response = await fetch('/api/projects');
        const data = await response.json();
        
        if (data.success) {
            projectData.projects = data.data;
            updateProjectSelector();
        } else {
            throw new Error(data.error || 'Failed to load projects');
        }
        
        // Load current project
        const currentResponse = await fetch('/api/projects/current');
        const currentData = await currentResponse.json();
        
        if (currentData.success) {
            projectData.currentProject = currentData.data;
            updateCurrentProjectDisplay();
        } else if (projectData.projects.length > 0) {
            // If no current project is set but we have projects, set the first one as current
            console.log('No current project set, setting first project as current...');
            await switchProject(projectData.projects[0].id);
        }
    } catch (error) {
        console.error('Error loading project data:', error);
    }
}

function updateProjectSelector() {
    const select = document.getElementById('projectSelect');
    if (!select) return;
    
    select.innerHTML = '<option value="">Select Project...</option>';
    
    projectData.projects.forEach(project => {
        const option = document.createElement('option');
        option.value = project.id;
        
        // Create a more descriptive display name
        let displayName = project.name;
        if (project.type === 'local') {
            // Show the folder name from the path for local projects
            const pathParts = project.path.split(/[\\\/]/);
            const folderName = pathParts[pathParts.length - 1];
            if (folderName && folderName !== project.name) {
                displayName = `${project.name} (${folderName})`;
            }
        } else if (project.type === 'github' || project.type === 'gitlab') {
            displayName = `${project.name} (${project.type})`;
        }
        
        option.textContent = displayName;
        if (projectData.currentProject && project.id === projectData.currentProject.id) {
            option.selected = true;
        }
        select.appendChild(option);
    });
}

function updateCurrentProjectDisplay() {
    const infoDiv = document.getElementById('currentProjectInfo');
    if (!infoDiv || !projectData.currentProject) return;
    
    const project = projectData.currentProject;
    const projectInfo = document.createElement('div');
    projectInfo.className = 'current-project-details';
    projectInfo.innerHTML = `
        <div class="project-info">
            <strong>${project.name}</strong> (${project.type})
            ${project.description ? `<br><small>${project.description}</small>` : ''}
        </div>
    `;
    
    // Replace existing project info
    const existingInfo = infoDiv.querySelector('.current-project-details');
    if (existingInfo) {
        existingInfo.remove();
    }
    infoDiv.appendChild(projectInfo);
}

async function switchProject() {
    const select = document.getElementById('projectSelect');
    const projectId = parseInt(select.value);
    
    if (!projectId) return;
    
    try {
        const response = await fetch(`/api/projects/${projectId}/switch`, {
            method: 'POST'
        });
        const data = await response.json();
        
        if (data.success) {
            projectData.currentProject = data.data;
            updateCurrentProjectDisplay();
            showSuccess(`Switched to project: ${data.data.name}`);
            loadGitData(); // Reload Git data for new project
        } else {
            throw new Error(data.error || 'Failed to switch project');
        }
    } catch (error) {
        console.error('Project switch error:', error);
        showError('Failed to switch project: ' + error.message);
    }
}

function toggleProjectTypeFields() {
    const projectType = document.getElementById('projectType').value;
    const localPathGroup = document.getElementById('localPathGroup');
    const repoUrlGroup = document.getElementById('repoUrlGroup');
    const localClonePathGroup = document.getElementById('localClonePathGroup');
    
    if (projectType === 'local') {
        localPathGroup.style.display = 'block';
        repoUrlGroup.style.display = 'none';
        localClonePathGroup.style.display = 'none';
    } else if (projectType === 'github' || projectType === 'gitlab') {
        localPathGroup.style.display = 'none';
        repoUrlGroup.style.display = 'block';
        localClonePathGroup.style.display = 'block';
    }
}

async function addProject() {
    const name = document.getElementById('projectName').value.trim();
    const type = document.getElementById('projectType').value;
    const description = document.getElementById('projectDescription').value.trim();
    const tags = document.getElementById('projectTags').value.trim().split(',').map(t => t.trim()).filter(t => t);
    
    if (!name) {
        showError('Please enter a project name');
        return;
    }
    
    const projectData = {
        name: name,
        type: type,
        description: description,
        tags: tags
    };
    
    if (type === 'local') {
        const path = document.getElementById('localPath').value.trim();
        if (!path) {
            showError('Please enter a local path');
            return;
        }
        projectData.path = path;
    } else if (type === 'github' || type === 'gitlab') {
        const repoUrl = document.getElementById('repoUrl').value.trim();
        if (!repoUrl) {
            showError('Please enter a repository URL');
            return;
        }
        projectData.repo_url = repoUrl;
        
        const localPath = document.getElementById('localClonePath').value.trim();
        if (localPath) {
            projectData.local_path = localPath;
        }
    }
    
    try {
        console.log('Sending project data:', projectData);
        const response = await fetch('/api/projects', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(projectData)
        });
        
        const data = await response.json();
        console.log('Response data:', data);
        
        if (data.success) {
            showSuccess('Project added successfully!');
            hideModal('addProjectModal');
            
            // Clear form
            document.getElementById('projectName').value = '';
            document.getElementById('projectDescription').value = '';
            document.getElementById('projectTags').value = '';
            document.getElementById('localPath').value = '';
            document.getElementById('repoUrl').value = '';
            document.getElementById('localClonePath').value = '';
            
            // Reload project data
            loadProjectData();
        } else {
            console.error('Server error:', data.error);
            showError(data.error || 'Failed to add project');
        }
    } catch (error) {
        console.error('Add project error:', error);
        showError('Failed to add project: ' + error.message);
    }
}

async function deleteProject(projectId) {
    if (!confirm('Are you sure you want to delete this project?')) {
        return;
    }
    
    try {
        const response = await fetch(`/api/projects/${projectId}`, {
            method: 'DELETE'
        });
        const data = await response.json();
        
        if (data.success) {
            showSuccess('Project deleted successfully!');
            loadProjectData();
        } else {
            throw new Error(data.error || 'Failed to delete project');
        }
    } catch (error) {
        console.error('Delete project error:', error);
        showError('Failed to delete project: ' + error.message);
    }
}

async function syncProject(projectId) {
    try {
        const response = await fetch(`/api/projects/${projectId}/sync`, {
            method: 'POST'
        });
        const data = await response.json();
        
        if (data.success) {
            showSuccess('Project synced successfully!');
        } else {
            throw new Error(data.error || 'Failed to sync project');
        }
    } catch (error) {
        console.error('Sync project error:', error);
        showError('Failed to sync project: ' + error.message);
    }
}

// Commit Diff Functions
async function showCommitDiff(commitHash, page = 1) {
    try {
        showModal('commitDiffModal');
        
        // Update modal title
        document.getElementById('commitDiffTitle').textContent = `📋 Commit Diff - ${commitHash.substring(0, 8)}`;
        
        // Show loading
        document.getElementById('commitDiffContent').innerHTML = '<div class="loading">Loading commit diff...</div>';
        
        // Fetch commit diff with pagination
        const response = await fetch(`/api/git/commit/${commitHash}/diff?page=${page}&per_page=${paginationState.diff.per_page}`);
        const data = await response.json();
        
        if (data.success) {
            displayCommitDiff(data.data, commitHash);
        } else {
            document.getElementById('commitDiffContent').innerHTML = `<div class="error">Error loading diff: ${data.error}</div>`;
        }
    } catch (error) {
        console.error('Error showing commit diff:', error);
        document.getElementById('commitDiffContent').innerHTML = `<div class="error">Error loading diff: ${error.message}</div>`;
    }
}

function displayCommitDiff(diffData, commitHash) {
    const { commit, files, diff, pagination } = diffData;
    
    let html = `
        <div class="commit-diff-header">
            <div class="commit-info">
                <strong>Commit:</strong> <span class="commit-hash">${commit.hash}</span>
            </div>
            <div class="commit-info">
                <strong>Author:</strong> ${commit.author} &lt;${commit.email}&gt;
            </div>
            <div class="commit-info">
                <strong>Date:</strong> ${commit.date}
            </div>
            <div class="commit-info">
                <strong>Message:</strong> ${commit.message}
            </div>
        </div>
    `;
    
    if (files && files.length > 0) {
        html += `
            <div class="files-changed">
                <h4>Files Changed (${files.length}):</h4>
        `;
        
        files.forEach(file => {
            const statusClass = file.status === 'A' ? 'added' : 
                               file.status === 'M' ? 'modified' : 
                               file.status === 'D' ? 'deleted' : '';
            const statusText = file.status === 'A' ? '+' : 
                              file.status === 'M' ? '~' : 
                              file.status === 'D' ? '-' : file.status;
            
            // Get diff content for this specific file
            const fileDiff = getFileDiff(diff, file.filename);
            
            // Since backend now filters files properly, if a file is shown, it has changes
            // Check if we have content for this specific page
            const hasContentOnThisPage = fileDiff && fileDiff.trim() !== '';
            
            html += `
                <div class="file-change" id="file-${file.filename.replace(/[^a-zA-Z0-9]/g, '_')}">
                    <div class="file-change-header" onclick="toggleFileDiff('${file.filename.replace(/[^a-zA-Z0-9]/g, '_')}')">
                        <span class="file-status ${statusClass}">${statusText}</span>
                        <span class="file-name">${file.filename}</span>
                        <span class="file-expand-icon">▶</span>
                    </div>
                    <div class="file-diff-content">
                        <div class="file-diff-header">📄 ${file.filename}</div>
                        <div class="diff-content" style="margin: 0; border: none; border-radius: 0;">
                            ${hasContentOnThisPage ? fileDiff : '<div class="diff-line context" style="padding: 20px; text-align: center; color: var(--text-secondary); font-style: italic;">This file\'s changes are on a different page. Use pagination controls to view all changes.</div>'}
                        </div>
                    </div>
                </div>
            `;
        });
        
        html += '</div>';
    } else {
        html += `
            <div class="files-changed">
                <div class="git-warning" style="padding: 20px; text-align: center; color: var(--text-secondary);">
                    No files with actual changes found in this commit.
                </div>
            </div>
        `;
    }
    
    // Add pagination controls for diff if needed
    if (pagination && pagination.pages > 1) {
        paginationState.diff = pagination;
        html += createDiffPaginationControls(commitHash, pagination);
    }
    
    document.getElementById('commitDiffContent').innerHTML = html;
}

function formatDiff(diffText) {
    const lines = diffText.split('\n');
    let html = '';
    let currentFile = '';
    let lineNumber = 0;
    
    lines.forEach(line => {
        lineNumber++;
        let className = 'context';
        let content = escapeHtml(line);
        
        if (line.startsWith('diff --git')) {
            // File header
            const match = line.match(/diff --git a\/(.*?) b\/(.*)/);
            if (match) {
                currentFile = match[1];
                html += `<div class="diff-file-header" data-line="${lineNumber}">📄 ${currentFile}</div>`;
                return;
            }
        } else if (line.startsWith('@@')) {
            // Hunk header
            className = 'header';
        } else if (line.startsWith('+') && !line.startsWith('+++')) {
            // Added line
            className = 'added';
        } else if (line.startsWith('-') && !line.startsWith('---')) {
            // Removed line
            className = 'removed';
        } else if (line.startsWith('index ') || line.startsWith('---') || line.startsWith('+++')) {
            // Skip metadata lines
            return;
        }
        
        html += `<div class="diff-line ${className}" data-line="${lineNumber}">${content}</div>`;
    });
    
    return html;
}

function getFileDiff(diffText, filename) {
    if (!diffText) return '';
    
    const lines = diffText.split('\n');
    let fileDiffLines = [];
    let inTargetFile = false;
    let foundFileHeader = false;
    
    lines.forEach(line => {
        if (line.startsWith('diff --git')) {
            // Check if this is the target file
            const match = line.match(/diff --git a\/(.*?) b\/(.*)/);
            if (match) {
                const fileA = match[1];
                const fileB = match[2];
                // Match the filename (handle renames and path changes)
                inTargetFile = (fileA === filename || fileB === filename || 
                              fileA.endsWith(filename) || fileB.endsWith(filename) ||
                              filename.endsWith(fileA) || filename.endsWith(fileB));
                foundFileHeader = inTargetFile;
            } else {
                inTargetFile = false;
            }
        } else if (inTargetFile) {
            // We're in the target file, collect all lines until next file
            if (line.startsWith('diff --git')) {
                inTargetFile = false;
                foundFileHeader = false;
            } else {
                fileDiffLines.push(line);
            }
        }
    });
    
    if (fileDiffLines.length === 0) return '';
    
    // Format the file-specific diff
    let html = '';
    let hasActualChanges = false;
    
    fileDiffLines.forEach(line => {
        let className = 'context';
        let content = escapeHtml(line);
        
        if (line.startsWith('@@')) {
            className = 'header';
        } else if (line.startsWith('+') && !line.startsWith('+++')) {
            className = 'added';
            hasActualChanges = true;
        } else if (line.startsWith('-') && !line.startsWith('---')) {
            className = 'removed';
            hasActualChanges = true;
        } else if (line.startsWith('index ') || line.startsWith('---') || line.startsWith('+++')) {
            return; // Skip metadata lines
        }
        
        html += `<div class="diff-line ${className}">${content}</div>`;
    });
    
    // If no actual changes found, return empty (this shouldn't happen with new backend filtering)
    return hasActualChanges ? html : '';
}

function toggleFileDiff(fileId) {
    const fileElement = document.getElementById(`file-${fileId}`);
    if (fileElement) {
        fileElement.classList.toggle('expanded');
    }
}

// Pagination helper functions
function createPaginationControls(type, pagination) {
    if (pagination.pages <= 1) return '';
    
    let html = `
        <div class="git-pagination" style="display: flex; justify-content: space-between; align-items: center; margin-top: 15px; padding: 10px; background: var(--bg-tertiary); border-radius: 8px; border: 1px solid var(--border-color);">
            <div class="pagination-info" style="font-size: 0.9em; color: var(--text-secondary);">
                Page ${pagination.page} of ${pagination.pages} (${pagination.total} ${type === 'files' ? 'files' : type === 'commits' ? 'commits' : 'lines'})
            </div>
            <div class="pagination-controls" style="display: flex; gap: 5px;">
    `;
    
    // Previous button
    if (pagination.has_prev) {
        html += `
            <button class="git-btn git-btn-secondary" onclick="goToPage('${type}', ${pagination.page - 1})" style="padding: 5px 10px; font-size: 0.8em;">
                ← Previous
            </button>
        `;
    } else {
        html += `
            <button class="git-btn git-btn-secondary" disabled style="padding: 5px 10px; font-size: 0.8em;">
                ← Previous
            </button>
        `;
    }
    
    // Page numbers (show up to 5 pages around current page)
    const startPage = Math.max(1, pagination.page - 2);
    const endPage = Math.min(pagination.pages, pagination.page + 2);
    
    for (let i = startPage; i <= endPage; i++) {
        if (i === pagination.page) {
            html += `
                <button class="git-btn git-btn-primary" style="padding: 5px 10px; font-size: 0.8em;">
                    ${i}
                </button>
            `;
        } else {
            html += `
                <button class="git-btn git-btn-secondary" onclick="goToPage('${type}', ${i})" style="padding: 5px 10px; font-size: 0.8em;">
                    ${i}
                </button>
            `;
        }
    }
    
    // Next button
    if (pagination.has_next) {
        html += `
            <button class="git-btn git-btn-secondary" onclick="goToPage('${type}', ${pagination.page + 1})" style="padding: 5px 10px; font-size: 0.8em;">
                Next →
            </button>
        `;
    } else {
        html += `
            <button class="git-btn git-btn-secondary" disabled style="padding: 5px 10px; font-size: 0.8em;">
                Next →
            </button>
        `;
    }
    
    html += `
            </div>
        </div>
    `;
    
    return html;
}

function goToPage(type, page) {
    if (type === 'files') {
        const filter = document.getElementById('fileStatusFilter');
        const statusFilter = filter ? filter.value : null;
        loadGitFiles(page, statusFilter);
    } else if (type === 'commits') {
        loadGitCommits(page);
    }
}

function filterFilesByStatus() {
    const filter = document.getElementById('fileStatusFilter');
    const statusFilter = filter ? filter.value : null;
    loadGitFiles(1, statusFilter); // Reset to page 1 when filtering
}

function createDiffPaginationControls(commitHash, pagination) {
    if (pagination.pages <= 1) return '';
    
    let html = `
        <div class="git-pagination" style="display: flex; justify-content: space-between; align-items: center; margin-top: 15px; padding: 10px; background: var(--bg-tertiary); border-radius: 8px; border: 1px solid var(--border-color);">
            <div class="pagination-info" style="font-size: 0.9em; color: var(--text-secondary);">
                Page ${pagination.page} of ${pagination.pages} (${pagination.total_lines} lines)
            </div>
            <div class="pagination-controls" style="display: flex; gap: 5px;">
    `;
    
    // Previous button
    if (pagination.has_prev) {
        html += `
            <button class="git-btn git-btn-secondary" onclick="showCommitDiff('${commitHash}', ${pagination.page - 1})" style="padding: 5px 10px; font-size: 0.8em;">
                ← Previous
            </button>
        `;
    } else {
        html += `
            <button class="git-btn git-btn-secondary" disabled style="padding: 5px 10px; font-size: 0.8em;">
                ← Previous
            </button>
        `;
    }
    
    // Page numbers (show up to 5 pages around current page)
    const startPage = Math.max(1, pagination.page - 2);
    const endPage = Math.min(pagination.pages, pagination.page + 2);
    
    for (let i = startPage; i <= endPage; i++) {
        if (i === pagination.page) {
            html += `
                <button class="git-btn git-btn-primary" style="padding: 5px 10px; font-size: 0.8em;">
                    ${i}
                </button>
            `;
        } else {
            html += `
                <button class="git-btn git-btn-secondary" onclick="showCommitDiff('${commitHash}', ${i})" style="padding: 5px 10px; font-size: 0.8em;">
                    ${i}
                </button>
            `;
        }
    }
    
    // Next button
    if (pagination.has_next) {
        html += `
            <button class="git-btn git-btn-secondary" onclick="showCommitDiff('${commitHash}', ${pagination.page + 1})" style="padding: 5px 10px; font-size: 0.8em;">
                Next →
            </button>
        `;
    } else {
        html += `
            <button class="git-btn git-btn-secondary" disabled style="padding: 5px 10px; font-size: 0.8em;">
                Next →
            </button>
        `;
    }
    
    html += `
            </div>
        </div>
    `;
    
    return html;
}

// Export functions for global access
window.refreshGitStatus = refreshGitStatus;
window.showModal = showModal;
window.hideModal = hideModal;
window.performCommit = performCommit;
window.performPull = performPull;
window.performPush = performPush;
window.switchBranch = switchBranch;
window.switchToBranch = switchToBranch;
window.deleteBranch = deleteBranch;
window.switchProject = switchProject;
window.toggleProjectTypeFields = toggleProjectTypeFields;
window.addProject = addProject;
window.deleteProject = deleteProject;
window.syncProject = syncProject;
window.showCommitDiff = showCommitDiff;
window.toggleFileDiff = toggleFileDiff;
window.goToPage = goToPage;
window.filterFilesByStatus = filterFilesByStatus;
