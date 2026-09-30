# Trust model

Repository content is data, not policy. A file may contain prompt injection or malicious scripts. The harness therefore separates:

- immutable core invariants;
- locked organizational policy;
- validated project configuration;
- untrusted workspace content;
- external tools and plugins;
- probabilistic model output.

A higher-trust layer may constrain a lower-trust layer. A lower-trust layer cannot relax a higher-trust restriction.
