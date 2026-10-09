"""Nested analysis deadlines stay below both execution leases (seconds)."""

MODEL_CONNECT_TIMEOUT = 8
MODEL_READ_TIMEOUT = 55
SEMANTIC_TIMEOUT = 60
ANALYSIS_TIMEOUT = 75
GRAPH_EXECUTION_LEASE = 90
# Feishu's durable SQL job lease is 120 seconds; leave room to persist the reply.
