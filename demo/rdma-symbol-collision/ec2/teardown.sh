#!/usr/bin/env bash
# Tear down the EC2 repro: terminate the instance, then delete the security
# group and key pair. Safe to run once you are done iterating.
set -uo pipefail
REGION=us-east-1
IID="${IID:-i-0921470a840598528}"
SG="${SG:-sg-0aa45b47a44de365c}"
KEYNAME="${KEYNAME:-howtf-rdma-key}"

echo "terminating $IID ..."
aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID" \
  --query 'TerminatingInstances[0].CurrentState.Name' --output text
aws ec2 wait instance-terminated --region "$REGION" --instance-ids "$IID"
echo "instance terminated."

# The SG can only be deleted after the instance is gone.
aws ec2 delete-security-group --region "$REGION" --group-id "$SG" && echo "deleted SG $SG"
aws ec2 delete-key-pair --region "$REGION" --key-name "$KEYNAME" && echo "deleted key pair $KEYNAME"
echo "teardown complete. (Also delete the private key in your scratchpad.)"
