"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 25.11.2025.
    File        : email_notifier
    Project     : Project-103
    Description : Email notification module. Sends email notifications for trading events.

    SPDX-License-Identifier: AGPL-3.0

"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.monitoring.alerts.alert_manager import TelegramAlert

logger = logging.getLogger(__name__)


class EmailNotifier:
    """Email notification handler"""

    def __init__(
        self,
        smtp_server: str,
        smtp_port: int,
        username: str,
        password: str,
        from_email: str,
        to_emails: list,
    ):
        """
        Initialize email notifier

        Args:
            smtp_server: SMTP server address
            smtp_port: SMTP port
            username: SMTP username
            password: SMTP password
            from_email: Sender email
            to_emails: List of recipient emails
        """
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.username = username
        self.password = password
        self.from_email = from_email
        self.to_emails = to_emails

    async def send_alert(self, alert: TelegramAlert) -> bool:
        """
        Send alert via email

        Args:
            alert: Alert to send

        Returns:
            bool: Success status
        """
        try:
            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = alert.title
            msg["From"] = self.from_email
            msg["To"] = ", ".join(self.to_emails)

            # Create text and HTML versions
            text = self._format_text(alert)
            html = self._format_html(alert)

            part1 = MIMEText(text, "plain")
            part2 = MIMEText(html, "html")

            msg.attach(part1)
            msg.attach(part2)

            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.username, self.password)
                server.send_message(msg)

            logger.info(f"Email alert sent: {alert.title}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email alert: {e}")
            return False

    def _format_text(self, alert: TelegramAlert) -> str:
        """Format plain text email"""
        text = f"{alert.title}\n\n{alert.message}\n\n"

        if alert.data:
            text += "Details:\n"
            for key, value in alert.data.items():
                text += f"  {key}: {value}\n"

        return text

    def _format_html(self, alert: TelegramAlert) -> str:
        """Format HTML email"""
        priority_colors = {
            "low": "#28a745",
            "medium": "#ffc107",
            "high": "#fd7e14",
            "critical": "#dc3545",
        }

        color = priority_colors.get(alert.priority, "#6c757d")

        html = f"""
        <html>
          <body style="font-family: Arial, sans-serif;">
            <div style="background-color: {color}; color: white; padding: 10px;">
              <h2>{alert.title}</h2>
            </div>
            <div style="padding: 20px;">
              <p>{alert.message}</p>
        """

        if alert.data:
            html += "<table style='border-collapse: collapse; width: 100%;'>"
            for key, value in alert.data.items():
                html += f"""
                <tr>
                  <td style='border: 1px solid #ddd; padding: 8px;'><strong>{key}</strong></td>
                  <td style='border: 1px solid #ddd; padding: 8px;'>{value}</td>
                </tr>
                """
            html += "</table>"

        html += """
            </div>
          </body>
        </html>
        """

        return html
