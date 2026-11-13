# Security

Report a reproducible vulnerability privately to the repository owner's contact address or GitHub private vulnerability reporting when available. Avoid posting credentials or sensitive paths in public issues.

The applications do not listen on a network port. They make outbound HTTPS requests to arXiv. UI communication uses local pipes and base64-encoded data; received text is not evaluated as Tcl or shell code. Keep TLS verification enabled.

Use a download directory owned by your account. The applications assume other local processes cannot maliciously modify that directory while a job is running. Subject folder traversal and symlink escapes are rejected. PDF validation checks a header, end marker, transfer size, and stored hash; it is not a malware scan or a complete PDF parser.
