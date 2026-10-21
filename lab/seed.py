from app.config import get_settings
from app.models import Conversation, Provider, User

LAB_EMAIL = "lab@local"


def _user(db):
    user = db.query(User).filter(User.email == LAB_EMAIL).first()
    if user is None:
        user = User(email=LAB_EMAIL)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _provider_row(db, user, name, base_url, api_key, priority):
    # les lignes sont recréées à chaque lancement : l'environnement du lab fait foi
    db.query(Provider).filter(Provider.user_id == user.id, Provider.name == name).delete()
    row = Provider(user_id=user.id, name=name, base_url=base_url or None, api_key=api_key or None,
                   is_active=True, priority=priority, config={})
    db.add(row)
    db.commit()
    return row


def seed(db, cfg, continue_last=False):
    """→ (user_id, conversation_id). `cfg` : voir lab.cli.LabConfig."""
    user = _user(db)
    _provider_row(db, user, cfg.provider, cfg.base_url, cfg.api_key, priority=10)

    verifier = get_settings().VERIFIER_PROVIDER_NAME.strip()
    if verifier and verifier != cfg.provider:
        # juge sur un AUTRE type de provider (ex. générateur lmstudio, juge openai) :
        # comme en prod, VERIFIER_PROVIDER_NAME désigne une ligne de la table providers
        _provider_row(db, user, verifier, cfg.verifier_base_url, cfg.verifier_api_key, priority=0)

    conversation = None
    if continue_last:
        conversation = (
            db.query(Conversation).filter(Conversation.user_id == user.id)
            .order_by(Conversation.created_at.desc()).first()
        )
    if conversation is None:
        conversation = Conversation(user_id=user.id, provider_name=cfg.provider, model=cfg.model)
        db.add(conversation)
    conversation.provider_name = cfg.provider
    conversation.model = cfg.model
    conversation.temperature = cfg.temperature          # transmis via la conversation, comme l'UI AgentRAG
    conversation.reasoning_mode = cfg.reasoning
    conversation.system_prompt = cfg.system_prompt
    db.commit()
    db.refresh(conversation)
    return user.id, conversation.id
