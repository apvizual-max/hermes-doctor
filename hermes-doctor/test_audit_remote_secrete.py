import sys
import importlib.util
from pathlib import Path
from unittest.mock import patch, MagicMock

# Load the module while mocking its side-effects on import
def load_module():
    with patch("builtins.print"), \
         patch("sys.exit"), \
         patch("pathlib.Path.read_text", return_value=""), \
         patch("subprocess.run", return_value=MagicMock(stdout="")):

        FILE_PATH = Path(__file__).resolve().parent / "audit_remote_secrete.py"
        spec = importlib.util.spec_from_file_location("audit_remote_secrete", FILE_PATH)
        module = importlib.util.module_from_spec(spec)
        sys.modules["audit_remote_secrete"] = module
        spec.loader.exec_module(module)
        return module

def test_classify():
    module = load_module()
    classify = module.classify

    # 1. CALE_ABSOLUTA
    assert classify("C:\\Program Files") == "CALE_ABSOLUTA"
    assert classify("d:/games") == "CALE_ABSOLUTA"

    # 2. CALE_RETEA
    assert classify("\\\\192.168.1.1\\share") == "CALE_RETEA"

    # 3. URL
    assert classify("http://localhost:8080") == "URL"
    assert classify("https://api.github.com/v3") == "URL"

    # 4. BOOLEAN
    for b in ("true", "false", "1", "0", "yes", "no"):
        assert classify(b) == "BOOLEAN"

    # 5. NUMAR/ID
    assert classify("123456") == "NUMAR/ID"
    assert classify("123.456") == "NUMAR/ID"
    assert classify("123-456-789") == "NUMAR/ID"
    assert classify("192.168.1.1/32") == "NUMAR/ID"

    # 6. POSIBIL_CREDENTIAL
    assert classify("a1b2c3d4e5f6g7h8i9j0") == "POSIBIL_CREDENTIAL"
    assert classify("ghp_1234567890abcdefghij") == "POSIBIL_CREDENTIAL"
    assert classify("123e4567-e89b-12d3-a456-426614174000") == "POSIBIL_CREDENTIAL" # UUID has letters and nums, len 36

    # 7. CONFIG
    assert classify("short123") == "CONFIG"
    assert classify("abcdefghijklmnopqrstuvwxyz") == "CONFIG"
    assert classify("12345678901234567890@#") == "CONFIG"
    assert classify("my_config_value") == "CONFIG"

if __name__ == "__main__":
    test_classify()
    print("All tests passed!")
