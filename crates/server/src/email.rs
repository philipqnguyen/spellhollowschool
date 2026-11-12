//! Outgoing email behind the [`Mailer`] trait.
//!
//! [`SmtpMailer`] sends through the mail server `SHS_SMTP_URL` names, from
//! `SHS_MAIL_FROM`. Without it, [`DevMailer`] writes each message to the log
//! (in development the verification link is also returned by the API).

use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;

use lettre::message::{Mailbox, header::ContentType};
use lettre::{AsyncSmtpTransport, AsyncTransport, Message, Tokio1Executor};

use crate::config::Config;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Email {
    pub to: String,
    pub subject: String,
    /// Plain-text body.
    pub text: String,
}

#[derive(Debug, thiserror::Error)]
#[error("sending mail failed: {0}")]
pub struct MailError(pub String);

pub type MailFuture<'a> = Pin<Box<dyn Future<Output = Result<(), MailError>> + Send + 'a>>;

/// Sends email. Object-safe so `AppState` can hold any implementation.
pub trait Mailer: Send + Sync + 'static {
    fn send(&self, email: Email) -> MailFuture<'_>;
}

/// Logs every message instead of sending it.
#[derive(Debug, Default, Clone, Copy)]
pub struct DevMailer;

impl Mailer for DevMailer {
    fn send(&self, email: Email) -> MailFuture<'_> {
        Box::pin(async move {
            tracing::info!(to = %email.to, subject = %email.subject, "email (not sent):\n{}", email.text);
            Ok(())
        })
    }
}

/// Sends through an SMTP server (a pooled connection, TLS by rustls).
pub struct SmtpMailer {
    transport: AsyncSmtpTransport<Tokio1Executor>,
    from: Mailbox,
}

impl SmtpMailer {
    /// `url`: `smtps://user:pass@host:465` or `smtp://user:pass@host:587?tls=required`
    /// (lettre's URL format). `from`: `Name <address>` or a bare address.
    pub fn new(url: &str, from: &str) -> Result<Self, MailError> {
        let transport = AsyncSmtpTransport::<Tokio1Executor>::from_url(url)
            .map_err(|e| MailError(format!("SHS_SMTP_URL: {e}")))?
            .build();
        let from = from
            .parse()
            .map_err(|e| MailError(format!("SHS_MAIL_FROM: {e}")))?;
        Ok(Self { transport, from })
    }
}

impl Mailer for SmtpMailer {
    fn send(&self, email: Email) -> MailFuture<'_> {
        Box::pin(async move {
            let to: Mailbox = email
                .to
                .parse()
                .map_err(|e| MailError(format!("recipient: {e}")))?;
            let message = Message::builder()
                .from(self.from.clone())
                .to(to)
                .subject(email.subject)
                .header(ContentType::TEXT_PLAIN)
                .body(email.text)
                .map_err(|e| MailError(e.to_string()))?;
            self.transport
                .send(message)
                .await
                .map(|_| ())
                .map_err(|e| MailError(e.to_string()))
        })
    }
}

/// The mailer for this deployment: SMTP when `SHS_SMTP_URL` is set, else the log.
pub fn from_config(config: &Config) -> Result<Arc<dyn Mailer>, MailError> {
    if let (Some(url), Some(from)) = (&config.smtp_url, &config.mail_from) {
        tracing::info!("sending mail by SMTP as {from}");
        return Ok(Arc::new(SmtpMailer::new(url.expose(), from)?));
    }
    if !config.dev {
        tracing::warn!(
            "no mail transport configured (SHS_SMTP_URL): verification emails are only logged"
        );
    }
    Ok(Arc::new(DevMailer))
}

/// The password reset email.
pub fn password_reset_email(to: &str, link: &str, valid_minutes: u64) -> Email {
    Email {
        to: to.to_owned(),
        subject: "Reset your SpellHollow School password".into(),
        text: format!(
            "Someone, probably you, asked to reset the password for this account.\n\n\
             Choose a new password here:\n\n{link}\n\n\
             The link works once and expires in {valid_minutes} minutes. Setting a new password \
             signs you out everywhere. If you didn't ask, you can ignore this message: \
             your password has not changed.\n"
        ),
    }
}

/// The verification email: sent on registering, and on signing in unconfirmed.
pub fn verification_email(to: &str, link: &str, valid_hours: u64) -> Email {
    Email {
        to: to.to_owned(),
        subject: "Confirm your email for SpellHollow School".into(),
        text: format!(
            "Welcome!\n\nConfirm your email address to finish creating your account:\n\n{link}\n\n\
             The link expires in {valid_hours} hours. If you did not sign up, you can ignore this message.\n"
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Building the connection pool needs a Tokio runtime (it connects lazily).
    #[tokio::test]
    async fn smtp_mailer_reads_its_settings() {
        assert!(
            SmtpMailer::new(
                "smtps://u:p@mail.example.org:465",
                "Game <no-reply@example.org>"
            )
            .is_ok()
        );
        assert!(
            SmtpMailer::new(
                "smtp://u:p@mail.example.org:587?tls=required",
                "no-reply@example.org"
            )
            .is_ok()
        );
        assert!(SmtpMailer::new("smtps://u:p@mail.example.org:465", "not an address").is_err());
        assert!(SmtpMailer::new("http://mail.example.org", "no-reply@example.org").is_err());
    }
}
