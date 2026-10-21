//! Failure kinds and their C4 exit codes.

/// Clean-failure category (C4 section 2).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Usage,
    Input,
    Format,
    Parse,
    Unstable,
}

impl Kind {
    /// Process exit code.
    pub fn code(self) -> u8 {
        match self {
            Kind::Usage => 2,
            Kind::Input => 3,
            Kind::Format => 4,
            Kind::Parse => 5,
            Kind::Unstable => 6,
        }
    }

    /// Value of the `error` field in the JSON document.
    pub fn name(self) -> &'static str {
        match self {
            Kind::Usage => "usage",
            Kind::Input => "input",
            Kind::Format => "format",
            Kind::Parse => "parse",
            Kind::Unstable => "unstable",
        }
    }
}

/// A clean failure: kind plus human detail (max 2000 chars).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Failure {
    pub kind: Kind,
    pub detail: String,
}

impl Failure {
    /// Build a failure, truncating `detail` to 2000 characters.
    pub fn new(kind: Kind, detail: impl AsRef<str>) -> Self {
        Failure {
            kind,
            detail: detail.as_ref().chars().take(2000).collect(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detail_truncated_by_chars() {
        let f = Failure::new(Kind::Parse, "é".repeat(3000));
        assert_eq!(f.detail.chars().count(), 2000);
        assert_eq!(f.kind.code(), 5);
    }
}
