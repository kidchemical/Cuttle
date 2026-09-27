/**
 * Cron countdown utilities for Cuttle.
 * Matches cuttle_daemon.py cron logic: min hr dom mon dow (5 fields).
 * dow: 0=Monday, 6=Sunday (Python weekday convention).
 */
(function (global) {
  'use strict';

  function parseCronField(field, value, minVal, maxVal) {
    if (field === '*') return true;
    if (field.includes('/')) {
      const [left, stepStr] = field.split('/', 2);
      const start = left === '*' ? minVal : Math.max(minVal, parseInt(left, 10));
      const step = parseInt(stepStr, 10);
      if (isNaN(step) || step <= 0) return false;
      return value >= start && (value - start) % step === 0;
    }
    if (field.includes(',')) {
      const vals = field.split(',').map(function (x) { return parseInt(x.trim(), 10); });
      return vals.indexOf(value) !== -1;
    }
    if (field.includes('-')) {
      const [a, b] = field.split('-', 2).map(function (x) { return parseInt(x, 10); });
      return a <= value && value <= b;
    }
    return value === parseInt(field, 10);
  }

  /** Day of week: 0=Monday, 6=Sunday (matches Python weekday). */
  function getCronDow(date) {
    var d = date.getDay(); // 0=Sun, 1=Mon, ..., 6=Sat
    return (d + 6) % 7;
  }

  function cronMatches(cronExpr, date) {
    try {
      var parts = cronExpr.trim().split(/\s+/);
      if (parts.length !== 5) return false;
      return (
        parseCronField(parts[0], date.getMinutes(), 0, 59) &&
        parseCronField(parts[1], date.getHours(), 0, 23) &&
        parseCronField(parts[2], date.getDate(), 1, 31) &&
        parseCronField(parts[3], date.getMonth() + 1, 1, 12) &&
        parseCronField(parts[4], getCronDow(date), 0, 6)
      );
    } catch (e) {
      return false;
    }
  }

  /**
   * Get the next Date when the cron will fire.
   * Starts from the start of the next minute. Returns null if invalid or not found within 366 days.
   */
  function getNextCronRun(cronExpr) {
    if (!cronExpr || typeof cronExpr !== 'string') return null;
    var now = new Date();
    var d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours(), now.getMinutes(), 0, 0);
    if (d <= now) d.setMinutes(d.getMinutes() + 1);
    var end = new Date(d.getTime());
    end.setFullYear(end.getFullYear() + 1);
    while (d < end) {
      if (cronMatches(cronExpr, d)) return new Date(d.getTime());
      d.setMinutes(d.getMinutes() + 1);
    }
    return null;
  }

  /**
   * Format ms until a target date as a human countdown string.
   */
  function formatCountdown(targetDate) {
    if (!targetDate || !(targetDate instanceof Date)) return null;
    var ms = targetDate.getTime() - Date.now();
    if (ms <= 0) return 'now';
    var sec = Math.floor(ms / 1000);
    var min = Math.floor(sec / 60);
    var hr = Math.floor(min / 60);
    var days = Math.floor(hr / 24);
    if (days > 0) return 'in ' + days + 'd ' + (hr % 24) + 'h';
    if (hr > 0) return 'in ' + hr + 'h ' + (min % 60) + 'm';
    if (min > 0) return 'in ' + min + 'm ' + (sec % 60) + 's';
    return 'in ' + sec + 's';
  }

  /**
   * Get countdown text for a cron expression, or null if invalid/disabled.
   */
  function getCronCountdown(cronExpr, enabled) {
    if (!enabled) return null;
    var next = getNextCronRun(cronExpr);
    if (!next) return null;
    return formatCountdown(next);
  }

  /**
   * Update all elements with data-cron and data-enabled to show live countdown.
   */
  function updateAllCountdowns() {
    var els = document.querySelectorAll('[data-cron][data-enabled="true"]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var cron = el.getAttribute('data-cron');
      var next = getNextCronRun(cron);
      var text = next ? formatCountdown(next) : null;
      el.textContent = text ? (' · Next ' + text) : '';
    }
  }

  global.CronCountdown = {
    getNextCronRun: getNextCronRun,
    formatCountdown: formatCountdown,
    getCronCountdown: getCronCountdown,
    updateAllCountdowns: updateAllCountdowns
  };
})(typeof window !== 'undefined' ? window : this);
