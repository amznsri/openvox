"""Telephony adapters — outbound dial-out + inbound webhook handling.

The voice WS pipeline is channel-agnostic; these modules adapt it to
specific carriers (Twilio today; Vonage/WhatsApp/Telegram TBD).
"""

from openvox.telephony.twilio import place_call

__all__ = ["place_call"]
