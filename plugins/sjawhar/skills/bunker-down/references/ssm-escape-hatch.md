# SSM escape hatch: policies and commands

Do not use `AmazonSSMManagedInstanceCore`: it grants 15 or more actions; the two policies below grant 5. Have the `cybersecurity-expert` agent (the `task` tool) review them before you apply them.

Trust policy (`aws:SourceAccount` blocks the confused-deputy problem). Replace `$ACCOUNT_ID` with the account number from Step 1: the JSON is passed inside single quotes, so the shell does not expand it.

```json
{"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
  "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole",
  "Condition": {"StringEquals": {"aws:SourceAccount": "$ACCOUNT_ID"}}}]}
```

Inline policy (Session Manager only, explicit deny on all else):

```json
{"Version": "2012-10-17", "Statement": [
  {"Sid": "SSMAgentHeartbeat", "Effect": "Allow", "Action": ["ssm:UpdateInstanceInformation"], "Resource": "*"},
  {"Sid": "SessionManagerChannels", "Effect": "Allow", "Resource": "*", "Action": [
    "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
    "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]},
  {"Sid": "DenyEverythingExceptSessionManager", "Effect": "Deny", "Resource": "*", "NotAction": [
    "ssm:UpdateInstanceInformation",
    "ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
    "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel"]}]}
```

Then create the role, attach it and install the plugin:

```bash
aws iam create-role --role-name EC2-SSM-SessionOnly \
  --assume-role-policy-document '<trust_policy_json>' \
  --tags Key=purpose,Value=ssm-session-only
aws iam put-role-policy --role-name EC2-SSM-SessionOnly \
  --policy-name SSMSessionManagerMinimal \
  --policy-document '<inline_policy_json>'
aws iam create-instance-profile --instance-profile-name EC2-SSM-SessionOnly
aws iam add-role-to-instance-profile --instance-profile-name EC2-SSM-SessionOnly \
  --role-name EC2-SSM-SessionOnly
sleep 10  # IAM propagation
aws ec2 associate-iam-instance-profile --instance-id $INSTANCE_ID \
  --iam-instance-profile Name=EC2-SSM-SessionOnly
sudo snap restart amazon-ssm-agent  # or sudo systemctl restart amazon-ssm-agent
sleep 15
aws ssm describe-instance-information --filters Key=InstanceIds,Values=$INSTANCE_ID
curl -so /tmp/session-manager-plugin.deb \
  "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_64bit/session-manager-plugin.deb"
sudo dpkg -i /tmp/session-manager-plugin.deb && rm /tmp/session-manager-plugin.deb
```
