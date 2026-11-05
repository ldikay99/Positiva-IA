"""
Modelos de correo electronico
"""
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class EmailAddress:
    """Direccion de correo electronico"""
    name: str = ""
    address: str = ""


@dataclass
class EmailBody:
    """Cuerpo del correo"""
    content_type: str = "html"  # 'html' o 'text'
    content: str = ""


@dataclass
class EmailAttachment:
    """Adjunto de correo"""
    id: str = ""
    name: str = ""
    content_type: str = ""
    size: int = 0
    content_bytes: Optional[str] = None
    is_inline: bool = False


@dataclass
class EmailMessage:
    """Mensaje de correo electronico"""
    id: str = ""
    subject: str = ""
    body: EmailBody = field(default_factory=EmailBody)
    from_address: Optional[EmailAddress] = None
    sender: Optional[EmailAddress] = None
    to_recipients: List[EmailAddress] = field(default_factory=list)
    received_datetime: str = ""
    sent_datetime: str = ""
    has_attachments: bool = False
    internet_message_id: str = ""
    is_read: bool = False
    conversation_id: Optional[str] = None
    categories: List[str] = field(default_factory=list)

    @classmethod
    def from_graph_api(cls, data: dict) -> "EmailMessage":
        """Crea un EmailMessage desde la respuesta de Microsoft Graph API"""
        from_data = data.get("from", {}).get("emailAddress", {})
        sender_data = data.get("sender", {}).get("emailAddress", {})

        return cls(
            id=data.get("id", ""),
            subject=data.get("subject", ""),
            body=EmailBody(
                content_type=data.get("body", {}).get("contentType", "html"),
                content=data.get("body", {}).get("content", ""),
            ),
            from_address=EmailAddress(
                name=from_data.get("name", ""),
                address=from_data.get("address", ""),
            ),
            sender=EmailAddress(
                name=sender_data.get("name", ""),
                address=sender_data.get("address", ""),
            ),
            to_recipients=[
                EmailAddress(
                    name=r.get("emailAddress", {}).get("name", ""),
                    address=r.get("emailAddress", {}).get("address", ""),
                )
                for r in data.get("toRecipients", [])
            ],
            received_datetime=data.get("receivedDateTime", ""),
            sent_datetime=data.get("sentDateTime", ""),
            has_attachments=data.get("hasAttachments", False),
            internet_message_id=data.get("internetMessageId", ""),
            is_read=data.get("isRead", False),
            conversation_id=data.get("conversationId"),
            categories=data.get("categories", []),
        )


@dataclass
class ExtractedUrl:
    """URL extraida del correo"""
    url: str
    type: str  # 'correo_certificado', 'descarga_directa', 'servicio_documentos', 'otro'
    priority: str  # 'alta', 'media', 'baja'


