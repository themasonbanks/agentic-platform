from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def embedded_code(name):
    command = ["ruby", "-rjson", "-ryaml", "-e",
               "puts JSON.generate(YAML.load_file(ARGV[0]))",
               str(ROOT / ".github/workflows/security-checks.yml")]
    workflow = json.loads(subprocess.check_output(command, text=True))
    matches = [item for job in workflow["jobs"].values() for item in job.get("steps", [])
               if item.get("name", item.get("id")) == name]
    assert len(matches) == 1
    step = matches[0]
    return step["run"].split("<<'PYCODE'\n", 1)[1].rsplit("PYCODE", 1)[0]


class CodeQLPolicyTests(unittest.TestCase):
    def exercise(self, severity=None, missing_rule=False, extension=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "codeql-results"
            results.mkdir()
            if severity is not None:
                rules = [] if missing_rule else [{"id": "sample-rule", "properties": {"security-severity": str(severity)}}]
                report = {"runs": [{"tool": {"driver": {"rules": rules}}, "results": [{"ruleId": "sample-rule"}]}]}
                if extension:
                    report["runs"][0]["tool"] = {"driver": {"rules": []}, "extensions": [{"name": "query-pack", "rules": rules}]}
                    report["runs"][0]["results"][0]["rule"] = {"id": "sample-rule", "index": 0, "toolComponent": {"index": 0}}
                (results / "result.sarif").write_text(json.dumps(report))
            return subprocess.run(["python3", "-c", embedded_code("Reject high severity code findings")], cwd=root, capture_output=True).returncode

    def test_published_reports_exclude_sensitive_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = "synthetic-private-message"
            (root / "gitleaks-raw.json").write_text(json.dumps([{
                "RuleID": "sample-rule", "File": "sample.txt", "StartLine": 1,
                "Message": marker, "Author": marker, "Email": marker,
                "Fragment": marker, "Secret": marker,
            }]))
            (root / "trivy-raw.json").write_text(json.dumps({"Results": [{
                "Target": "main.tf", "Type": "terraform",
                "Misconfigurations": [{"ID": "sample-rule", "Severity": "HIGH", "Status": "FAIL", "Message": marker, "CauseMetadata": {"Code": marker}}],
            }]}))
            output = root / "published"
            subprocess.run(["python3", "-c", embedded_code("Prepare report sanitizer"), str(root), str(output)], check=True)
            for report in output.glob("*.json"):
                self.assertNotIn(marker, report.read_text())

    def test_language_detection_fails_for_missing_adapter(self):
        for suffix, expected in ((".py", 0), (".sh", 0), (".go", 1)):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
                (root / ("sample" + suffix)).write_text("")
                subprocess.run(["git", "add", "."], cwd=root, check=True)
                result = subprocess.run(["python3", "-c", embedded_code("detect")], cwd=root,
                                        env=dict(os.environ, GITHUB_OUTPUT=str(root / "output")), capture_output=True)
                self.assertEqual(result.returncode, expected)

    def test_high_severity_blocks(self):
        self.assertNotEqual(self.exercise(7), 0)

    def test_lower_severity_does_not_block(self):
        self.assertEqual(self.exercise(6.9), 0)

    def test_extension_metadata_is_resolved(self):
        self.assertEqual(self.exercise(6.9, extension=True), 0)
        self.assertNotEqual(self.exercise(7, extension=True), 0)
        self.assertNotEqual(self.exercise(9, True, extension=True), 0)

    def test_missing_output_or_metadata_fails(self):
        self.assertNotEqual(self.exercise(), 0)
        self.assertNotEqual(self.exercise(9, True), 0)


if __name__ == "__main__":
    unittest.main()
