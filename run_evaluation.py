"""End-to-end evaluation: run the 20-question labeled set through
retrieve -> generate -> score, and write the report.

Usage:
  python run_evaluation.py                  # full set (20 LLM calls)
  python run_evaluation.py --only q01 q02   # subset
"""
import argparse

from app.evaluation.runner import run_evaluation


def main():
    ap = argparse.ArgumentParser(description="End-to-end evaluation")
    ap.add_argument("--only", nargs="+", dest="only",
                    help="only these question ids (e.g. q01 q02 a01)")
    args = ap.parse_args()
    run_evaluation(question_ids=args.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
