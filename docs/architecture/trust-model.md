# Trust model

Repository content is data, not policy. A file may contain prompt injection or malicious scripts. The harness therefore separates:

- immutable core invariants;
- locked organizational policy;
- validated project configuration;
- untrusted workspace content;
- external tools and plugins;
- probabilistic model output.

A higher-trust layer may constrain a lower-trust layer. A lower-trust layer cannot relax a higher-trust restriction.

## Protection of excluded paths

`runtime/guard.py` (`governance.protectExcludedPaths`) fingerprints the places the ChangeSet leaves out
(`.git/hooks`, virtual environments, `dist`, ignored files) before and after each agent invocation. The
fingerprint of a file is its **size, its modification time in nanoseconds and the SHA-256 of its first
8 KB** (8192 bytes); for a symbolic link it is the target. It detects every write that changes the size,
the time or the start of the file. A write that keeps the size, restores the modification time and
leaves the first 8 KB intact is **not** detected: the guard is a cheap tripwire for a large tree, not
a content hash.
