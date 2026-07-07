"""Shared fixtures: a fake case bundle mirroring SFDC field shapes."""

import pytest


@pytest.fixture
def sample_case():
    return {
        "Id": "500Kh00000jNc7mIAC",
        "CaseNumber": "5400813446",
        "Subject": "ezkf-agent starting issue after reboot of node",
        "Issue__c": (
            "When ezkf-agent services is restarted on one of the master nodes, "
            "ezkf-agent goes to failed state with iptables-restore: line 12 failed."
        ),
        "Case_description__c": "",
        "Cause__c": (
            "After the worker node reboot the kernel iptables state is clean, all "
            "custom chains are gone, but the saved rules file still references them."
        ),
        "Resolution__c": (
            "1. Backup or mv the current ezkf-rules.v4 files to another location. "
            "2. Reset the ezkf-agent services with systemctl reset-failed ezkf-agent.service. "
            "3. Start the ezkf-agent which will recreate the ezkf-rules.v4 files."
        ),
        "Severity__c": "Critical",
        "Priority": "High",
        "Status": "Closed",
        "AccountName__c": "Hewlett-Packard Enterprises, LLC",
        "RecordType": {"Name": "GSD CSC Case Closed"},
        "Environment__c": "PCAI 1.x",
        "CreatedDate": "2026-01-25T00:24:00.000+0000",
        "ClosedDate": "2026-01-27T10:26:00.000+0000",
    }


@pytest.fixture
def sample_tasks():
    return [
        {
            "Id": "00TKh00003AtOdLMAV",
            "Subject": "Troubleshooting",
            "Description": (
                "Root Cause: stale iptables rules after node reboot.\n"
                "1. Verify all master nodes are Ready.\n"
                "2. Confirm calico pods running.\n"
                "3. Recreate ezkf-rules.v4 and restart service."
            ),
            "Status": "Completed",
            "Category__c": "Elevation",
            "Log_Action_Type__c": "Escalation",
        }
    ]


@pytest.fixture
def sample_comments():
    return [
        {
            "Id": "c1",
            "CommentBody": "Have taken handover of the case and sent meeting invite.",
            "CreatedDate": "2026-01-26T09:49:00.000+0000",
        }
    ]
