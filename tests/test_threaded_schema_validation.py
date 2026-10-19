"""A valid file in one batch worker cannot erase a concurrent file's XSD errors."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from spec_surgeon.verify import xsd

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
GOOD = f'<w:document xmlns:w="{W_NS}"><w:body/></w:document>'.encode()
BAD = f'<w:document xmlns:w="{W_NS}" bad="yes"><w:body/></w:document>'.encode()


def test_invalid_file_retains_its_errors_when_valid_worker_runs_before_error_log_is_read(monkeypatch):
    validated_bad, validated_good = Event(), Event()
    original = xsd._schema

    class ScheduledValidation:
        def __init__(self, validator):
            self.validator = validator

        def validate(self, root):
            result = self.validator.validate(root)
            if root.get("bad") is not None:
                validated_bad.set()
                assert validated_good.wait(10)
            else:
                assert validated_bad.wait(10)
                validated_good.set()
            return result

        @property
        def error_log(self):
            return self.validator.error_log

    # Force the precise interleaving that erased failures with the previous global XMLSchema cache.
    monkeypatch.setattr(xsd, "_schema", lambda rel: ScheduledValidation(original(rel)))
    with ThreadPoolExecutor(max_workers=2) as workers:
        bad = workers.submit(xsd.validate_part, "word/document.xml", BAD)
        assert validated_bad.wait(10)
        good = workers.submit(xsd.validate_part, "word/document.xml", GOOD)
        bad_errors, good_errors = bad.result(), good.result()
    assert good_errors == set()
    assert bad_errors and any("bad" in message for message in bad_errors)
    baseline, output, introduced = xsd.compare("word/document.xml", GOOD, BAD)
    assert baseline == set() and output and introduced == output
