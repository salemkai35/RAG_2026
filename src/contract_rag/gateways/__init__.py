"""Clients des API internes de modèles (format compatible OpenAI).

Un seul endroit du code parle aux modèles : remplacer un modèle ou un endpoint
ne demande qu'un changement de configuration.
"""

from .http import ModelEndpointError

__all__ = ["ModelEndpointError"]
