#!/usr/bin/env python3
"""
Task Management System for Cuttle
Handles task creation, tracking, and management with database persistence
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import List, Dict, Optional, Any
from pathlib import Path

_TASKS_DB_PATH = str(Path(__file__).resolve().parents[1] / "data" / "db" / "tasks.db")


class TaskManager:
    def __init__(self, db_path: str = None):
        """Initialize the TaskManager with database connection"""
        self.db_path = db_path or _TASKS_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_database()
    
    def init_database(self):
        """Initialize the database with required tables"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Create tasks table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT UNIQUE NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    owner TEXT DEFAULT '',
                    status TEXT DEFAULT 'None',
                    priority TEXT DEFAULT 'Medium',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Create comments table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS task_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    author TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
                )
            ''')
            
            # Create indexes for better performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks (owner)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks (created_at)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_comments_task_id ON task_comments (task_id)')
            
            conn.commit()
    
    def _generate_task_id(self) -> str:
        """Generate a unique task ID"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Get all existing task_ids to find the highest numeric ID
            cursor.execute('SELECT task_id FROM tasks WHERE task_id LIKE "T%"')
            existing_ids = cursor.fetchall()
            
            max_id = 0
            for (task_id,) in existing_ids:
                if task_id and task_id.startswith('T') and len(task_id) == 7:
                    try:
                        numeric_id = int(task_id[1:])
                        max_id = max(max_id, numeric_id)
                    except ValueError:
                        continue
            
            return f"T{max_id + 1:06d}"
    
    def _update_timestamp(self, task_id: int):
        """Update the updated_at timestamp for a task"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'UPDATE tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                (task_id,)
            )
            conn.commit()
    
    def create_task(self, title: str, description: str = '', owner: str = '', 
                   status: str = 'None', priority: str = 'Medium') -> Dict[str, Any]:
        """Create a new task"""
        task_id = self._generate_task_id()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tasks (task_id, title, description, owner, status, priority)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (task_id, title, description, owner, status, priority))
            
            new_task_id = cursor.lastrowid
            conn.commit()
        
        # Return the created task
        return self.get_task(new_task_id)
    
    def get_task(self, task_id: int) -> Optional[Dict[str, Any]]:
        """Get a specific task by ID"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM tasks WHERE id = ?', (task_id,))
            task_row = cursor.fetchone()
            
            if not task_row:
                return None
            
            # Get comments for this task
            cursor.execute('''
                SELECT * FROM task_comments 
                WHERE task_id = ? 
                ORDER BY created_at ASC
            ''', (task_id,))
            comment_rows = cursor.fetchall()
            
            # Convert to dictionary
            task = dict(task_row)
            task['comments'] = []
            for comment in comment_rows:
                comment_dict = dict(comment)
                # Convert comment timestamp to ISO format for JavaScript compatibility
                if comment_dict.get('created_at'):
                    comment_dict['createdAt'] = datetime.fromisoformat(comment_dict['created_at']).isoformat() + 'Z'
                task['comments'].append(comment_dict)
            
            # Convert field names from snake_case to camelCase for JavaScript compatibility
            if task.get('task_id'):
                task['taskId'] = task['task_id']
            if task.get('created_at'):
                task['createdAt'] = datetime.fromisoformat(task['created_at']).isoformat() + 'Z'
            if task.get('updated_at'):
                task['updatedAt'] = datetime.fromisoformat(task['updated_at']).isoformat() + 'Z'
            
            return task
    
    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """Get all tasks with their comments"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM tasks ORDER BY created_at DESC')
            task_rows = cursor.fetchall()
            
            tasks = []
            for task_row in task_rows:
                task_id = task_row['id']
                
                # Get comments for this task
                cursor.execute('''
                    SELECT * FROM task_comments 
                    WHERE task_id = ? 
                    ORDER BY created_at ASC
                ''', (task_id,))
                comment_rows = cursor.fetchall()
                
                # Convert to dictionary
                task = dict(task_row)
                task['comments'] = []
                for comment in comment_rows:
                    comment_dict = dict(comment)
                    # Convert comment timestamp to ISO format for JavaScript compatibility
                    if comment_dict.get('created_at'):
                        comment_dict['createdAt'] = datetime.fromisoformat(comment_dict['created_at']).isoformat() + 'Z'
                    task['comments'].append(comment_dict)
                
                # Convert field names from snake_case to camelCase for JavaScript compatibility
                if task.get('task_id'):
                    task['taskId'] = task['task_id']
                if task.get('created_at'):
                    task['createdAt'] = datetime.fromisoformat(task['created_at']).isoformat() + 'Z'
                if task.get('updated_at'):
                    task['updatedAt'] = datetime.fromisoformat(task['updated_at']).isoformat() + 'Z'
                
                tasks.append(task)
            
            return tasks
    
    def update_task(self, task_id: int, title: str, description: str = '', 
                   owner: str = '', status: str = 'None', priority: str = 'Medium') -> Optional[Dict[str, Any]]:
        """Update an existing task"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE tasks 
                SET title = ?, description = ?, owner = ?, status = ?, priority = ?
                WHERE id = ?
            ''', (title, description, owner, status, priority, task_id))
            
            if cursor.rowcount == 0:
                return None
            
            conn.commit()
        
        # Update timestamp
        self._update_timestamp(task_id)
        
        # Return the updated task
        return self.get_task(task_id)
    
    def delete_task(self, task_id: int) -> bool:
        """Delete a task and all its comments"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Delete comments first (due to foreign key constraint)
            cursor.execute('DELETE FROM task_comments WHERE task_id = ?', (task_id,))
            
            # Delete the task
            cursor.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
            
            success = cursor.rowcount > 0
            conn.commit()
            
            return success
    
    def add_comment(self, task_id: int, content: str, author: str = 'Anonymous') -> Optional[Dict[str, Any]]:
        """Add a comment to a task"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO task_comments (task_id, author, content)
                VALUES (?, ?, ?)
            ''', (task_id, author, content))
            
            comment_id = cursor.lastrowid
            conn.commit()
        
        # Update task timestamp
        self._update_timestamp(task_id)
        
        # Return the created comment
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM task_comments WHERE id = ?', (comment_id,))
            comment_row = cursor.fetchone()
            
            if comment_row:
                comment = dict(comment_row)
                # Convert comment timestamp to ISO format for JavaScript compatibility
                if comment.get('created_at'):
                    comment['createdAt'] = datetime.fromisoformat(comment['created_at']).isoformat() + 'Z'
                return comment
            return None
    
    def delete_comment(self, task_id: int, comment_id: int) -> bool:
        """Delete a comment from a task"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # First verify that the task exists and the comment belongs to it
            cursor.execute('''
                SELECT tc.id FROM task_comments tc 
                JOIN tasks t ON tc.task_id = t.id 
                WHERE tc.id = ? AND tc.task_id = ?
            ''', (comment_id, task_id))
            
            if not cursor.fetchone():
                return False
            
            # Delete the comment
            cursor.execute('DELETE FROM task_comments WHERE id = ?', (comment_id,))
            
            success = cursor.rowcount > 0
            conn.commit()
            
            if success:
                # Update task timestamp
                self._update_timestamp(task_id)
            
            return success
    
    def get_tasks_by_status(self, status: str) -> List[Dict[str, Any]]:
        """Get all tasks with a specific status"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC', (status,))
            task_rows = cursor.fetchall()
            
            tasks = []
            for task_row in task_rows:
                task_id = task_row['id']
                
                # Get comments for this task
                cursor.execute('''
                    SELECT * FROM task_comments 
                    WHERE task_id = ? 
                    ORDER BY created_at ASC
                ''', (task_id,))
                comment_rows = cursor.fetchall()
                
                # Convert to dictionary
                task = dict(task_row)
                task['comments'] = []
                for comment in comment_rows:
                    comment_dict = dict(comment)
                    # Convert comment timestamp to ISO format for JavaScript compatibility
                    if comment_dict.get('created_at'):
                        comment_dict['createdAt'] = datetime.fromisoformat(comment_dict['created_at']).isoformat() + 'Z'
                    task['comments'].append(comment_dict)
                
                # Convert field names from snake_case to camelCase for JavaScript compatibility
                if task.get('task_id'):
                    task['taskId'] = task['task_id']
                if task.get('created_at'):
                    task['createdAt'] = datetime.fromisoformat(task['created_at']).isoformat() + 'Z'
                if task.get('updated_at'):
                    task['updatedAt'] = datetime.fromisoformat(task['updated_at']).isoformat() + 'Z'
                
                tasks.append(task)
            
            return tasks
    
    def get_tasks_by_owner(self, owner: str) -> List[Dict[str, Any]]:
        """Get all tasks assigned to a specific owner"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute('SELECT * FROM tasks WHERE owner = ? ORDER BY created_at DESC', (owner,))
            task_rows = cursor.fetchall()
            
            tasks = []
            for task_row in task_rows:
                task_id = task_row['id']
                
                # Get comments for this task
                cursor.execute('''
                    SELECT * FROM task_comments 
                    WHERE task_id = ? 
                    ORDER BY created_at ASC
                ''', (task_id,))
                comment_rows = cursor.fetchall()
                
                # Convert to dictionary
                task = dict(task_row)
                task['comments'] = []
                for comment in comment_rows:
                    comment_dict = dict(comment)
                    # Convert comment timestamp to ISO format for JavaScript compatibility
                    if comment_dict.get('created_at'):
                        comment_dict['createdAt'] = datetime.fromisoformat(comment_dict['created_at']).isoformat() + 'Z'
                    task['comments'].append(comment_dict)
                
                # Convert field names from snake_case to camelCase for JavaScript compatibility
                if task.get('task_id'):
                    task['taskId'] = task['task_id']
                if task.get('created_at'):
                    task['createdAt'] = datetime.fromisoformat(task['created_at']).isoformat() + 'Z'
                if task.get('updated_at'):
                    task['updatedAt'] = datetime.fromisoformat(task['updated_at']).isoformat() + 'Z'
                
                tasks.append(task)
            
            return tasks
    
    def search_tasks(self, query: str) -> List[Dict[str, Any]]:
        """Search tasks by title, description, or task ID"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            search_term = f"%{query}%"
            cursor.execute('''
                SELECT * FROM tasks 
                WHERE title LIKE ? OR description LIKE ? OR task_id LIKE ?
                ORDER BY created_at DESC
            ''', (search_term, search_term, search_term))
            
            task_rows = cursor.fetchall()
            
            tasks = []
            for task_row in task_rows:
                task_id = task_row['id']
                
                # Get comments for this task
                cursor.execute('''
                    SELECT * FROM task_comments 
                    WHERE task_id = ? 
                    ORDER BY created_at ASC
                ''', (task_id,))
                comment_rows = cursor.fetchall()
                
                # Convert to dictionary
                task = dict(task_row)
                task['comments'] = []
                for comment in comment_rows:
                    comment_dict = dict(comment)
                    # Convert comment timestamp to ISO format for JavaScript compatibility
                    if comment_dict.get('created_at'):
                        comment_dict['createdAt'] = datetime.fromisoformat(comment_dict['created_at']).isoformat() + 'Z'
                    task['comments'].append(comment_dict)
                
                # Convert field names from snake_case to camelCase for JavaScript compatibility
                if task.get('task_id'):
                    task['taskId'] = task['task_id']
                if task.get('created_at'):
                    task['createdAt'] = datetime.fromisoformat(task['created_at']).isoformat() + 'Z'
                if task.get('updated_at'):
                    task['updatedAt'] = datetime.fromisoformat(task['updated_at']).isoformat() + 'Z'
                
                tasks.append(task)
            
            return tasks
    
    def get_task_statistics(self) -> Dict[str, Any]:
        """Get task statistics"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # Total tasks
            cursor.execute('SELECT COUNT(*) FROM tasks')
            total_tasks = cursor.fetchone()[0]
            
            # Tasks by status
            cursor.execute('''
                SELECT status, COUNT(*) as count 
                FROM tasks 
                GROUP BY status
            ''')
            status_counts = dict(cursor.fetchall())
            
            # Tasks by priority
            cursor.execute('''
                SELECT priority, COUNT(*) as count 
                FROM tasks 
                GROUP BY priority
            ''')
            priority_counts = dict(cursor.fetchall())
            
            # Recent activity (last 7 days)
            cursor.execute('''
                SELECT COUNT(*) FROM tasks 
                WHERE created_at >= datetime('now', '-7 days')
            ''')
            recent_tasks = cursor.fetchone()[0]
            
            return {
                'total_tasks': total_tasks,
                'status_counts': status_counts,
                'priority_counts': priority_counts,
                'recent_tasks': recent_tasks
            }
    
    def export_tasks(self, format: str = 'json') -> str:
        """Export all tasks to a file"""
        tasks = self.get_all_tasks()
        
        if format.lower() == 'json':
            export_data = {
                'export_date': datetime.now().isoformat(),
                'tasks': tasks
            }
            return json.dumps(export_data, indent=2, default=str)
        else:
            raise ValueError(f"Unsupported export format: {format}")
    
    def import_tasks(self, data: str, format: str = 'json') -> int:
        """Import tasks from a file"""
        if format.lower() == 'json':
            import_data = json.loads(data)
            tasks = import_data.get('tasks', [])
        else:
            raise ValueError(f"Unsupported import format: {format}")
        
        imported_count = 0
        for task_data in tasks:
            try:
                self.create_task(
                    title=task_data['title'],
                    description=task_data.get('description', ''),
                    owner=task_data.get('owner', ''),
                    status=task_data.get('status', 'None'),
                    priority=task_data.get('priority', 'Medium')
                )
                imported_count += 1
            except Exception as e:
                print(f"Failed to import task {task_data.get('task_id', 'unknown')}: {e}")
        
        return imported_count

# Global task manager instance
task_manager = TaskManager()

# Convenience functions for backward compatibility
def create_task(title: str, description: str = '', owner: str = '', 
               status: str = 'None', priority: str = 'Medium') -> Dict[str, Any]:
    """Create a new task"""
    return task_manager.create_task(title, description, owner, status, priority)

def get_task(task_id: int) -> Optional[Dict[str, Any]]:
    """Get a specific task"""
    return task_manager.get_task(task_id)

def get_all_tasks() -> List[Dict[str, Any]]:
    """Get all tasks"""
    return task_manager.get_all_tasks()

def update_task(task_id: int, title: str, description: str = '', 
               owner: str = '', status: str = 'None', priority: str = 'Medium') -> Optional[Dict[str, Any]]:
    """Update a task"""
    return task_manager.update_task(task_id, title, description, owner, status, priority)

def delete_task(task_id: int) -> bool:
    """Delete a task"""
    return task_manager.delete_task(task_id)

def add_comment(task_id: int, content: str, author: str = 'Anonymous') -> Optional[Dict[str, Any]]:
    """Add a comment to a task"""
    return task_manager.add_comment(task_id, content, author)

def delete_comment(task_id: int, comment_id: int) -> bool:
    """Delete a comment from a task"""
    return task_manager.delete_comment(task_id, comment_id)

if __name__ == '__main__':
    # Test the task manager
    print("Testing Task Manager...")
    
    # Create some test tasks
    task1 = create_task("Test Task 1", "This is a test task", "John Doe", "In Progress", "High")
    task2 = create_task("Test Task 2", "Another test task", "Jane Smith", "Planned", "Medium")
    
    print(f"Created task: {task1['task_id']}")
    print(f"Created task: {task2['task_id']}")
    
    # Add comments
    add_comment(task1['id'], "This is a test comment", "John Doe")
    add_comment(task1['id'], "Another comment", "Jane Smith")
    
    # Get all tasks
    all_tasks = get_all_tasks()
    print(f"Total tasks: {len(all_tasks)}")
    
    # Get statistics
    stats = task_manager.get_task_statistics()
    print(f"Statistics: {stats}")
    
    print("Task Manager test completed!")
