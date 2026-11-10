# Fuzz harnesses

[Atheris](https://github.com/google/atheris) harnesses for the functions that read input an agent
or a tool influences: validator output, SARIF reports and the secret redactor. Each one checks a
property, not only the absence of a crash. They run on Linux in `.github/workflows/fuzz.yml`
(Atheris ships Linux wheels only); pytest does not collect them.

```bash
python -m pip install --require-hashes -r requirements/fuzz.txt
python -m pip install --no-deps -e .
python tests/fuzz/fuzz_parsers.py -max_total_time=60
python tests/fuzz/fuzz_sarif.py -max_total_time=60
python tests/fuzz/fuzz_redaction.py -max_total_time=60
```
