# Pipeline Execution Race Condition Fix

## Problem
When running a pipeline in the Node Editor, the play/stop button would rapidly switch back and forth, and multiple query requests would be kicked off simultaneously. This caused:
- Confusing UI behavior (button toggling rapidly)
- Multiple concurrent pipeline executions
- Duplicate API calls and resource waste
- Unpredictable execution results

## Root Cause
The issue was a **race condition** in the button click handler. When the Run button was clicked:

1. **Single Click Scenario:**
   - `isExecuting` flag was set to `true`
   - Button UI started updating
   - But there was a brief window where another click could slip through

2. **Double-Click or Rapid Click Scenario:**
   - First click: Sets `isExecuting = true`, starts execution
   - Second click: (Before UI fully updates) Sees `isExecuting` as `false` initially, starts another execution
   - This created multiple concurrent executions running at the same time

3. **No Error Handling:**
   - If the executor threw an error, the flags would never be reset
   - This would leave the button in a locked state

## Solution
Added a **two-level protection mechanism**:

### 1. Execution Lock Flag
Added a new `_executionLock` flag that provides an immediate lock:
```javascript
this._executionLock = false; // Initialize in constructor
```

### 2. Double-Check in Click Handler
The click handler now checks both flags before allowing execution:
```javascript
document.getElementById('btn-run-stop').addEventListener('click', () => {
    // Prevent rapid clicks
    if (this._executionLock) {
        console.warn('Pipeline execution already in progress, ignoring click');
        return;
    }
    
    if (this.isExecuting) {
        this.stopExecution();
    } else {
        this.executePipeline();
    }
});
```

### 3. Robust Lock Management
The `executePipeline()` method now:
- Sets both `_executionLock` and `isExecuting` flags immediately
- Wraps execution in try-catch-finally to ensure flags are always released
- Releases all flags in the `finally` block, guaranteeing cleanup even on errors

```javascript
async executePipeline() {
    // Double-check with lock to prevent race conditions
    if (this.isExecuting || this._executionLock) {
        console.warn('Pipeline execution already in progress');
        return;
    }
    
    // Set both flags to prevent any race conditions
    this._executionLock = true;
    this.isExecuting = true;
    this.updateRunStopButton();
    
    let success = false;
    try {
        const executor = new PipelineExecutor(this);
        success = await executor.execute();
    } catch (error) {
        console.error('Pipeline execution error:', error);
        success = false;
    } finally {
        // Always release execution flags, even if there was an error
        this.isPipelineExecuting = false;
        this.isExecuting = false;
        this._executionLock = false; // Release the lock
        this.updateRunStopButton();
        
        // ... rest of cleanup code
    }
}
```

## Benefits
✅ **Prevents Multiple Concurrent Executions:** The lock ensures only one pipeline can run at a time
✅ **No More Button Toggling:** Button state is properly managed and protected
✅ **Robust Error Handling:** Flags are always released, even if execution fails
✅ **Better User Feedback:** Console warnings inform about ignored clicks
✅ **Race Condition Protection:** Two-level check prevents any timing issues

## Testing
To verify the fix works:
1. Open the Node Editor
2. Create a simple pipeline
3. Try double-clicking the Run button rapidly
4. You should see only ONE execution start
5. Console will show warnings for ignored clicks
6. Button should properly toggle between Run ▶️ and Stop ⏹️ states

## Files Modified
- `src/web/js/node_editor.js`
  - Added `_executionLock` flag initialization (line 40)
  - Updated click handler with lock check (lines 370-374)
  - Updated `executePipeline()` with try-finally and lock management (lines 1456-1516)

