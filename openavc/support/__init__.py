"""Reports a person sends to OpenAVC: the problem report and its upload.

- ``problem_report``: builds one device's problem report (a zip, the device
  audit report's sibling) from what the platform already recorded.
- ``report_upload``: the one door a report leaves the system through, for a
  problem report and a device audit report alike. Nothing calls it on its
  own: a person pressed Send.
"""
