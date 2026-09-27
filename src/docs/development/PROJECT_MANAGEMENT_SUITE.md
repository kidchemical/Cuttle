# 🚀 Cuttle Project Management Suite

A comprehensive project management system integrated into Cuttle that allows you to manage both local and remote projects from a unified web interface.

## ✨ Features

### 📁 **Multi-Project Support**
- **Local Projects**: Track and manage local development projects
- **GitHub Integration**: Clone and manage GitHub repositories
- **GitLab Integration**: Clone and manage GitLab repositories
- **Project Switching**: Seamlessly switch between different projects
- **Project History**: Track all project activities and changes

### 🔧 **Git Operations**
- **Repository Status**: View current branch, working directory status, and staging area
- **Branch Management**: Create, switch, and delete branches
- **Commit Management**: Stage files and create commits with custom messages
- **Remote Operations**: Pull from and push to remote repositories
- **File Status**: View modified, added, deleted, and untracked files

### 🎨 **Modern Web Interface**
- **Responsive Design**: Works on desktop and mobile devices
- **Dark/Light Theme**: Matches your existing Cuttle theme
- **Real-time Updates**: Auto-refresh every 30 seconds
- **Interactive Modals**: Clean, accessible interface for all operations
- **Project Selector**: Easy project switching with dropdown

## 🚀 Quick Start

### 1. **Start the Web Server**
```bash
python web_chat_api.py
```

### 2. **Access the Project Management UI**
Open your browser and navigate to:
```
http://localhost:8080/git_ui.html
```

### 3. **Add Your First Project**

#### **Local Project**
1. Click "➕ Add Project"
2. Select "Local Project"
3. Enter project name and local path
4. Add description and tags (optional)
5. Click "Add Project"

#### **GitHub Project**
1. Click "➕ Add Project"
2. Select "GitHub Repository"
3. Enter repository URL (e.g., `https://github.com/username/repo.git`)
4. Optionally specify local clone path
5. Add description and tags
6. Click "Add Project"

#### **GitLab Project**
1. Click "➕ Add Project"
2. Select "GitLab Repository"
3. Enter repository URL (e.g., `https://gitlab.com/username/repo.git`)
4. Optionally specify local clone path
5. Add description and tags
6. Click "Add Project"

### 4. **Switch Between Projects**
Use the project selector dropdown in the header to switch between different projects. All Git operations will be performed on the currently selected project.

## 📋 Project Types

### 🏠 **Local Projects**
- **Use Case**: Existing local development projects
- **Requirements**: Valid local directory path
- **Features**: Full Git operations, file management
- **Example**: `C:\Users\YourName\Projects\MyApp`

### 🐙 **GitHub Projects**
- **Use Case**: GitHub repositories you want to work with
- **Requirements**: Valid GitHub repository URL
- **Features**: Auto-clone, remote sync, full Git operations
- **Example**: `https://github.com/microsoft/vscode.git`

### 🦊 **GitLab Projects**
- **Use Case**: GitLab repositories you want to work with
- **Requirements**: Valid GitLab repository URL
- **Features**: Auto-clone, remote sync, full Git operations
- **Example**: `https://gitlab.com/gitlab-org/gitlab.git`

## 🔧 Git Operations

### **Repository Status**
- View current branch
- Check working directory status
- See staging area information
- Monitor file changes

### **Branch Management**
- **Switch Branch**: Change to existing branch
- **Create Branch**: Create and switch to new branch
- **Delete Branch**: Remove unused branches

### **Commit Operations**
- **Stage Files**: Select specific files to commit
- **Commit Message**: Write descriptive commit messages
- **View History**: See recent commits with details

### **Remote Operations**
- **Pull**: Fetch and merge changes from remote
- **Push**: Send local commits to remote repository
- **Sync**: Update remote projects with latest changes

## 🗄️ Database Schema

The project management system uses SQLite for data persistence:

### **Projects Table**
```sql
CREATE TABLE projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    type TEXT NOT NULL,  -- 'local', 'github', 'gitlab'
    path TEXT,           -- Local path or remote URL
    description TEXT,
    tags TEXT,           -- JSON array of tags
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_accessed TIMESTAMP,
    is_active BOOLEAN DEFAULT 0,
    config TEXT          -- JSON config for project-specific settings
);
```

### **Project History Table**
```sql
CREATE TABLE project_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER,
    action TEXT,         -- 'switched_to', 'created', 'updated', 'deleted'
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    details TEXT,        -- JSON details about the action
    FOREIGN KEY (project_id) REFERENCES projects (id)
);
```

## 🔌 API Endpoints

### **Project Management**
- `GET /api/projects` - Get all projects
- `GET /api/projects/current` - Get current project
- `GET /api/projects/<id>` - Get specific project
- `POST /api/projects` - Create new project
- `PUT /api/projects/<id>` - Update project
- `DELETE /api/projects/<id>` - Delete project
- `POST /api/projects/<id>/switch` - Switch to project
- `POST /api/projects/<id>/sync` - Sync remote project
- `GET /api/projects/history` - Get project history
- `GET /api/projects/stats` - Get project statistics

### **Git Operations**
- `GET /api/git/status` - Get repository status
- `GET /api/git/branches` - Get branches
- `GET /api/git/commits` - Get recent commits
- `GET /api/git/files` - Get working directory files
- `POST /api/git/commit` - Commit changes
- `POST /api/git/pull` - Pull from remote
- `POST /api/git/push` - Push to remote
- `POST /api/git/branch` - Branch operations

## 🎯 Use Cases

### **Development Team**
- Manage multiple client projects
- Switch between different repositories
- Track project activity and history
- Collaborate on shared repositories

### **Open Source Contributor**
- Manage forks of popular projects
- Track your contributions across repositories
- Sync with upstream changes
- Organize projects by technology or purpose

### **Freelancer/Consultant**
- Organize client projects
- Track project progress and changes
- Manage both local and remote repositories
- Maintain project history for billing

### **Student/Learner**
- Organize learning projects
- Practice with different repositories
- Track progress across multiple courses
- Experiment with different technologies

## 🔒 Security Considerations

- **Local Path Validation**: Ensures paths exist and are accessible
- **Git Command Sanitization**: Prevents command injection
- **Input Validation**: Validates all user inputs
- **Error Handling**: Graceful error handling with user feedback
- **Database Security**: SQLite with parameterized queries

## 🚀 Future Enhancements

### **Planned Features**
- **SSH Key Management**: Secure authentication for private repositories
- **Project Templates**: Pre-configured project setups
- **Team Collaboration**: Multi-user project sharing
- **CI/CD Integration**: Build and deployment status
- **Project Analytics**: Usage statistics and insights
- **Backup/Restore**: Project data backup functionality

### **Additional Remote Support**
- **Bitbucket Integration**: Atlassian Bitbucket support
- **Azure DevOps**: Microsoft Azure DevOps integration
- **Self-hosted Git**: Support for self-hosted Git servers
- **FTP/SFTP**: Legacy file transfer protocol support

## 🛠️ Technical Details

### **Dependencies**
- **Python 3.7+**: Core runtime
- **SQLite3**: Database storage
- **Flask**: Web framework
- **Git**: Version control operations
- **Requests**: HTTP client for remote operations

### **File Structure**
```
├── project_manager.py          # Core project management logic
├── web_chat_api.py            # API endpoints and web server
├── web/git_ui.html            # Project management UI
├── web/js/git_ui.js           # Frontend JavaScript
├── projects.db                # SQLite database (auto-created)
└── example_project_setup.py   # Example setup script
```

## 📚 Examples

### **Python Script Usage**
```python
from project_manager import project_manager

# Add a local project
project_manager.add_local_project(
    name="My Web App",
    path="/path/to/project",
    description="My awesome web application",
    tags=["python", "flask", "web"]
)

# Add a GitHub project
project_manager.add_github_project(
    name="React Component Library",
    repo_url="https://github.com/facebook/react.git",
    description="Facebook's React library",
    tags=["javascript", "react", "ui"]
)

# Switch to a project
project_manager.switch_to_project(project_id=1)

# Get current project
current = project_manager.get_current_project()
print(f"Working on: {current['name']}")
```

### **API Usage**
```bash
# Get all projects
curl http://localhost:8080/api/projects

# Create a new project
curl -X POST http://localhost:8080/api/projects \
  -H "Content-Type: application/json" \
  -d '{"name": "My Project", "type": "local", "path": "/path/to/project"}'

# Switch to a project
curl -X POST http://localhost:8080/api/projects/1/switch

# Get Git status for current project
curl http://localhost:8080/api/git/status
```

## 🎉 Getting Started

1. **Run the example setup**:
   ```bash
   python example_project_setup.py
   ```

2. **Start the web server**:
   ```bash
   python web_chat_api.py
   ```

3. **Open the Project Management UI**:
   ```
   http://localhost:8080/git_ui.html
   ```

4. **Add your projects and start managing them!**

---

**Happy coding with Cuttle Project Management Suite! 🚀**
