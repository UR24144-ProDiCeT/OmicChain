"""Run the OmicChain tests by group and summarise the result (exit code 1 if a test file fails).

  python tests/run_all.py fast       tests without R (mock Rscript): Linux / macOS
  python tests/run_all.py portable   pure Python tests: also on Windows
  python tests/run_all.py upstream   tests with the real Rsubread (R + Bioconductor needed), long
  python tests/run_all.py all        fast + upstream
"""
import subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORTABLE = ["test_inputs", "test_chain_publish", "test_fetch", "test_jobs"]
FAST = PORTABLE + ["test_covariate", "test_verify", "test_data_origin", "test_identity_integration", "test_qc_history",
                   "test_app_publish", "test_app_design", "test_app_explore", "test_app_matrix", "test_app_orcid",
                   "test_app_rerun", "test_app_rerun_raw"]
UPSTREAM = ["test_reference", "test_upstream", "test_app_reference", "test_app_geo", "test_app_annotate",
            "test_app_local", "test_replay"]
GROUPS = {"portable": PORTABLE, "fast": FAST, "upstream": UPSTREAM, "all": FAST + UPSTREAM}


def main(argv):
    group = argv[0] if argv else "fast"
    if group not in GROUPS:
        sys.exit(__doc__)
    failed = []
    for name in GROUPS[group]:
        t0 = time.time()
        r = subprocess.run([sys.executable, str(HERE / f"{name}.py")], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        ok = r.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({time.time() - t0:.0f} s)", flush=True)
        if not ok:
            failed.append(name)
            lines = [l for l in (r.stdout + r.stderr).splitlines() if "ScriptRunContext" not in l]
            print("\n".join("      " + l for l in lines[-40:]), flush=True)
    print(f"\n{len(GROUPS[group]) - len(failed)} / {len(GROUPS[group])} test files passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
