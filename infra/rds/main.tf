terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
}

variable "name" { type = string }
variable "subnet_ids" { type = list(string) }
variable "vpc_security_group_ids" { type = list(string) }
variable "instance_class" {
  type    = string
  default = "db.m6g.large"
}
variable "allocated_storage" {
  type    = number
  default = 100
}
variable "read_replica_count" {
  type    = number
  default = 1
}
variable "alarm_actions" {
  type    = list(string)
  default = []
}

# Logs slow statements and lock waits, forces TLS, and caps runaway sessions.
resource "aws_db_parameter_group" "this" {
  name   = "${var.name}-pg16"
  family = "postgres16"

  parameter {
    name  = "log_min_duration_statement"
    value = "500"
  }
  parameter {
    name  = "log_lock_waits"
    value = "1"
  }
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
  parameter {
    name         = "idle_in_transaction_session_timeout"
    value        = "60000"
    apply_method = "immediate"
  }
  parameter {
    name         = "shared_preload_libraries"
    value        = "pg_stat_statements"
    apply_method = "pending-reboot"
  }
}

resource "aws_db_subnet_group" "this" {
  name       = var.name
  subnet_ids = var.subnet_ids
}

resource "aws_db_instance" "primary" {
  identifier                          = "${var.name}-primary"
  engine                              = "postgres"
  engine_version                      = "16"
  instance_class                      = var.instance_class
  allocated_storage                   = var.allocated_storage
  max_allocated_storage               = var.allocated_storage * 4
  storage_type                        = "gp3"
  storage_encrypted                   = true
  multi_az                            = true
  db_subnet_group_name                = aws_db_subnet_group.this.name
  vpc_security_group_ids              = var.vpc_security_group_ids
  parameter_group_name                = aws_db_parameter_group.this.name
  manage_master_user_password         = true # master password lives in Secrets Manager, not in state
  username                            = "dbadmin"
  backup_retention_period             = 14 # enables PITR to any second in the window
  copy_tags_to_snapshot               = true
  deletion_protection                 = true
  skip_final_snapshot                 = false
  final_snapshot_identifier           = "${var.name}-final"
  performance_insights_enabled        = true
  enabled_cloudwatch_logs_exports     = ["postgresql", "upgrade"]
  auto_minor_version_upgrade          = true
  iam_database_authentication_enabled = true
}

resource "aws_db_instance" "replica" {
  count                  = var.read_replica_count
  identifier             = "${var.name}-replica-${count.index}"
  replicate_source_db    = aws_db_instance.primary.identifier
  instance_class         = var.instance_class
  storage_encrypted      = true
  vpc_security_group_ids = var.vpc_security_group_ids
  parameter_group_name   = aws_db_parameter_group.this.name
  skip_final_snapshot    = true
}

resource "aws_cloudwatch_metric_alarm" "replica_lag" {
  count               = var.read_replica_count
  alarm_name          = "${var.name}-replica-${count.index}-lag"
  namespace           = "AWS/RDS"
  metric_name         = "ReplicaLag"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.replica[count.index].identifier }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 30
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = var.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "free_storage" {
  alarm_name          = "${var.name}-free-storage-low"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.primary.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 10737418240
  comparison_operator = "LessThanThreshold"
  alarm_actions       = var.alarm_actions
}

# Transaction ID age: the early warning for wraparound when vacuum is blocked.
resource "aws_cloudwatch_metric_alarm" "txid_age" {
  alarm_name          = "${var.name}-maximum-used-transaction-ids"
  namespace           = "AWS/RDS"
  metric_name         = "MaximumUsedTransactionIDs"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.primary.identifier }
  statistic           = "Maximum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1000000000
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = var.alarm_actions
}

output "primary_endpoint" { value = aws_db_instance.primary.endpoint }
output "replica_endpoints" { value = aws_db_instance.replica[*].endpoint }
output "master_secret_arn" { value = aws_db_instance.primary.master_user_secret[0].secret_arn }
