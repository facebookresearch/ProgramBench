class EvalStepError(Exception):
    """Raised when a required step in Evaluator fails."""

    def __init__(self, error_code: str, error_details: str = ""):
        super().__init__(error_code)
        self.error_code = error_code
        self.error_details = error_details


class EmptyTestResultError(EvalStepError):
    """Raised when test results XML is empty."""

    def __init__(self, msg: str = ""):
        super().__init__("empty_test_results", msg)


class XmlParseError(EvalStepError):
    """Raised when the test results XML is present but malformed."""

    def __init__(self, msg: str = ""):
        super().__init__("xml_parse_error", msg)
