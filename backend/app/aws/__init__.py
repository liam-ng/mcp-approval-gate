"""The only package in the backend that talks to AWS.

Everything else in `app/` is credential-free by design — `core/aws_schema.py`
reads botocore's bundled JSON and never builds a client, precisely so the gate
needs no AWS trust to do its job. This package is the one, deliberate
exception, it is read-only, and it is off unless AWS_DISCOVERY_ENABLED is set.
Keeping it in its own package is what makes that boundary visible in the import
graph: if `boto3.client(` appears anywhere outside here, that is a bug.
"""
