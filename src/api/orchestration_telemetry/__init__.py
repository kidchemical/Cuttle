"""Orchestration + supervision telemetry: an append-only event log written by
host code paths (sub-agent rounds, supervision checks and actions), read by
the Orchestration dashboard. Recorders never raise into their callers."""
