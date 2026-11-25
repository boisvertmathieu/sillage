from sillage.redact import pseudonymize, redact
from sillage.signatures import command_identifiers, exit_code, primary_command, signature


def test_redact_secrets_and_pii():
    text = (
        "token ghp_abcdefghijklmnopqrstuvwxyz0123 courriel jean.tremblay@exemple.qc.ca "
        "ip 10.12.3.4 password=SuperSecret123 Authorization: Bearer abcdefghijklmnop.qrstu "
        "AKIAABCDEFGHIJKLMNOP /home/jtremblay/src/app /mnt/c/Users/JTremblay/projets "
        "tel 514-555-1234 carte 4111 1111 1111 1111 nas 046 454 286 "
        "postgres://admin:motdepasse@db.interne:5432/app"
    )
    out = redact(text, max_len=2000)
    for leaked in [
        "ghp_",
        "jean.tremblay",
        "10.12.3.4",
        "SuperSecret123",
        "abcdefghijklmnop",
        "AKIA",
        "jtremblay",
        "JTremblay",
        "514-555-1234",
        "4111",
        "046 454 286",
        "motdepasse",
    ]:
        assert leaked not in out, leaked
    assert "[JETON_GITHUB]" in out and "[COURRIEL]" in out and "[CARTE]" in out and "[NAS]" in out


def test_redact_keeps_ordinary_numbers():
    assert redact("Tests: 142 passed in 3.2s") == "Tests: 142 passed in 3.2s"


def test_redact_truncates():
    assert len(redact("x" * 1000, max_len=50)) == 50


def test_pseudonymize_is_stable_and_salted():
    assert pseudonymize("alex@poste", "a") == pseudonymize("alex@poste", "a")
    assert pseudonymize("alex@poste", "a") != pseudonymize("alex@poste", "b")


def test_signature_groups_variants():
    a = signature("Error: ENOENT: no such file or directory, open '/home/x/app/config.json'")
    b = signature("Error: ENOENT: no such file or directory, open '/home/y/other/settings.json'")
    assert a == b
    assert "<" in a


def test_signature_keeps_root_cause_of_long_chains():
    msg = (
        "[ERROR] Failed to execute goal on project api: Could not resolve dependencies for project a:b:jar:1.0: "
        "Failed to collect dependencies at x:y:jar:2: PKIX path building failed: unable to find valid "
        "certification path to requested target"
    )
    sig = signature(msg)
    assert sig.startswith("[ERROR] Failed to execute goal")
    assert sig.endswith("certification path to requested target")
    assert len(sig) <= 145


def test_signature_picks_error_line():
    out = 'compiling...\nok\nnpm error Missing script: "test"\nnpm error\n<exited with exit code 1>'
    assert signature(out) == 'npm error Missing script: "<val>"'


def test_command_identifiers():
    assert command_identifiers("cd app && npm run test:unit -- --watch") == ["cd", "npm run test:unit"]
    assert command_identifiers("FOO=1 sudo ./gradlew build") == ["./gradlew build"]
    assert command_identifiers("kubectl -n prod get pods") == ["kubectl get"]
    assert command_identifiers("mvn -s ~/.m2/settings.xml test") == ["mvn test"]
    assert command_identifiers("python -m pytest -q tests/") == ["python -m pytest"]
    assert primary_command("cd x; echo go; git status | head") == "git status"
    assert command_identifiers("") == []


def test_exit_code():
    assert exit_code("blah\n<exited with exit code 2>") == 2
    assert exit_code("completed with exit code 0") == 0
    assert exit_code("rien") is None
