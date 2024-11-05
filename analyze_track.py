import argparse
import logging
import sys

from src.gpx_track_analyzer import TrackAnalyzer

logging.basicConfig(format="%(asctime)s %(levelname)8s %(pathname)s: %(message)s", level=logging.INFO,
                    datefmt="%y-%m-%dT%H:%M:%S")
_LOGGER = logging.getLogger(__name__)


def main():
    args = _parse_arguments()
    analyzer = TrackAnalyzer(args.input_file)
    analyzer.analyze()
    analyzer.write_data_and_extension_to_file(args.output_file)


def _parse_arguments():
    parser = argparse.ArgumentParser(description="Analyze given track.")
    parser.add_argument("--input_file", help="File to analyze", default="/home/prinzt/Downloads/2021-06-13_Um_den_Hohen_Göll.gpx")
    parser.add_argument("--output_file", help="File to analyze", default="/tmp/output.gpx")

    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(main())
