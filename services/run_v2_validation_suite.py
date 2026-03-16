import argparse
from services.v2_validation_suite import run_v2_validation_suite


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run physical validation suite for vulcanization solver v2.")
    parser.add_argument("--quick", action="store_true", help="Run reduced scenarios for faster feedback.")
    parser.add_argument(
        "--out-dir",
        default="data/out",
        help="Directory for JSON/CSV validation reports.",
    )
    parser.add_argument(
        "--sim-out-dir",
        default=None,
        help="Optional directory for temporary simulation .npz outputs.",
    )
    parser.add_argument(
        "--report-stem",
        default="v2_validation_report",
        help="Base filename for report outputs.",
    )
    parser.add_argument(
        "--gate-mode",
        choices=["informative", "strict"],
        default="informative",
        help="Quality gate behavior: informative (never fail) or strict (fail on critical quality/all_required=false).",
    )
    args = parser.parse_args(argv)

    report = run_v2_validation_suite(
        output_dir=args.out_dir,
        report_stem=args.report_stem,
        quick=bool(args.quick),
        sim_out_dir=args.sim_out_dir,
        gate_mode=str(args.gate_mode),
    )

    print("v2_validation_suite: DONE")
    print(f"  quick_mode: {report['quick_mode']}")
    print(f"  quality_gate_mode: {report['quality_gate_mode']}")
    print(f"  total_cases: {report['total_cases']}")
    print(f"  all_required_passed: {report['all_required_passed']}")
    print(f"  has_critical_quality: {report['has_critical_quality']}")
    print(f"  ci_gate_passed: {report['ci_gate_passed']}")
    print(f"  json_report: {report['json_report_path']}")
    print(f"  csv_report: {report['csv_report_path']}")


if __name__ == "__main__":
    main()
