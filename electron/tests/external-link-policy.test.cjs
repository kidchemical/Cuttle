'use strict';
// Unit tests for ../external-link-policy.js. Run: node --test tests/external-link-policy.test.cjs
const assert = require('node:assert/strict');
const { describe, it } = require('node:test');
const { routeWindowOpen } = require('../external-link-policy.js');

const APP = 'http://127.0.0.1:8000/app_shell.html';

describe('routeWindowOpen', () => {
  it('keeps same-origin app pages in-app', () => {
    assert.equal(routeWindowOpen('http://127.0.0.1:8000/query_log.html?id=7', APP), 'in-app');
  });

  it('sends cross-origin links to the OS browser', () => {
    assert.equal(routeWindowOpen('https://assetstore.unity.com/packages/1', APP), 'external');
  });

  it('sends non-http schemes to the OS handler', () => {
    assert.equal(routeWindowOpen('mailto:someone@example.com', APP), 'external');
  });

  it('allows about:blank through', () => {
    assert.equal(routeWindowOpen('about:blank', APP), 'in-app');
  });

  it('denies unparseable URLs', () => {
    assert.equal(routeWindowOpen('::not a url::', APP), 'deny');
  });
});
