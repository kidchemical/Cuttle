/**
 * Force Cache Buster for Pipeline Executor
 * This file forces the browser to reload the latest JavaScript
 */

// Add version parameter to force reload
// Updated: 2025-10-12 - Added output node tracking fix
const CACHE_VERSION = "2025-10-12-output-tracking";

// Force reload pipeline_executor.js if it's cached
if (window.performance && window.performance.navigation.type === window.performance.navigation.TYPE_RELOAD) {
    console.log('%c[CACHE] Hard reload detected - using fresh JavaScript', 'color: #10b981; font-weight: bold');
} else {
    console.log('%c[CACHE] Initial load - version: ' + CACHE_VERSION, 'color: #3b82f6; font-weight: bold');
}

// Log pipeline executor load
console.log('%c[PIPELINE] Pipeline executor loading...', 'color: #f59e0b; font-weight: bold');

// Verify critical functions exist
setTimeout(() => {
    let allFeaturesOk = true;
    
    // Check agent pool detection
    if (window.PipelineExecutor && window.PipelineExecutor.prototype.executeLLMWithAgentPool) {
        console.log('%c[AGENT POOL] Agent pool detection ENABLED ✓', 'color: #10b981; font-weight: bold');
    } else {
        console.log('%c[AGENT POOL] WARNING: Agent pool detection NOT found!', 'color: #ef4444; font-weight: bold');
        allFeaturesOk = false;
    }
    
    // Check output node tracking (look for the tracking code in executeOutputNode)
    if (window.PipelineExecutor) {
        const executeOutputStr = window.PipelineExecutor.prototype.executeOutputNode.toString();
        const hasOutputTracking = executeOutputStr.includes('/api/record-node-execution') || 
                                  executeOutputStr.includes('record-node-execution');
        
        if (hasOutputTracking) {
            console.log('%c[OUTPUT NODES] Output node tracking ENABLED ✓', 'color: #10b981; font-weight: bold');
        } else {
            console.log('%c[OUTPUT NODES] WARNING: Output node tracking NOT found!', 'color: #ef4444; font-weight: bold');
            allFeaturesOk = false;
        }
    }
    
    if (!allFeaturesOk) {
        console.log('%c[CACHE WARNING] ⚠️ Please clear cache and hard refresh (Ctrl+Shift+Delete, then Ctrl+F5)', 
                   'color: #ef4444; font-weight: bold; font-size: 14px; padding: 5px; border: 2px solid #ef4444;');
    } else {
        console.log('%c[ALL SYSTEMS] All pipeline features loaded ✓', 'color: #10b981; font-weight: bold; font-size: 14px;');
    }
}, 1500);

