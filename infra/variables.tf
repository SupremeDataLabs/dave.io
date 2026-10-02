variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "project_name" {
  type    = string
  default = "ask-dave"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.project_name))
    error_message = "Use 3-20 lowercase letters, digits or hyphens; start with a letter."
  }
}

variable "enable_chat" {
  description = "False deploys the history-only first milestone; true requires a funded LLM key."
  type        = bool
  default     = true
}

variable "llm_api_key" {
  description = "Provided only in memory by deploy.py; never saved to state or plans."
  type        = string
  sensitive   = true
  ephemeral   = true
  default     = ""
}

variable "llm_key_version" {
  description = "Increment to rotate the write-only key; keep stable on ordinary redeploys."
  type        = number
  default     = 1
}

variable "llm_model" {
  type    = string
  default = "gpt-4.1-mini"
}

variable "alarm_action_arns" {
  description = "Optional existing SNS/action ARNs; without these alarms still enter ALARM in CloudWatch."
  type        = list(string)
  default     = []
}
