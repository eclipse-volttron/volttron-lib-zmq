class BaseServerAuthentication(BaseAuthentication):
    def __init__(self, auth_service=None) -> None:
        super(BaseServerAuthentication, self).__init__()
        self.auth_service = auth_service
        self.authorization = None

    def setup_authentication(self):
        pass

    def handle_authentication(self, protected_topics):
        pass

    def stop_authentication(self):
        pass

    def unbind_authentication(self):
        pass


# BaseAuthorization class
class BaseServerAuthorization:
    def __init__(self,
                 auth_service=None
                 ):
        self.auth_service = auth_service

    def approve_authorization(self, user_id):
        pass

    def deny_authorization(self, user_id):
        pass

    def delete_authorization(self, user_id):
        pass

    def get_authorization(self, user_id):
        pass

    def get_authorization_status(self, user_id):
        pass

    def get_pending_authorizations(self):
        pass

    def get_approved_authorizations(self):
        pass

    def get_denied_authorizations(self):
        pass

    def update_user_capabilites(self, user_to_caps):
        pass

    def load_protected_topics(self, protected_topics_data):
        return jsonapi.loads(protected_topics_data) if protected_topics_data else {}

    def update_protected_topics(self, protected_topics):
        pass
