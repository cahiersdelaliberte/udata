# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from udata import search
from udata.models import Organization
from udata.core.site.views import current_site

from . import metrics  # Metrics are need for the mapping

__all__ = ('OrganizationSearch', )


max_reuses = lambda: max(current_site.metrics.get('max_org_reuses'), 10)
max_datasets = lambda: max(current_site.metrics.get('max_org_datasets'), 10)
max_followers = lambda: max(current_site.metrics.get('max_org_followers'), 10)


class OrganizationSearch(search.ModelSearchAdapter):
    model = Organization
    fuzzy = True
    fields = (
        'name^6',
        'description',
    )
    sorts = {
        'name': search.Sort('name.raw'),
        'reuses': search.Sort('metrics.reuses'),
        'datasets': search.Sort('metrics.datasets'),
        'followers': search.Sort('metrics.followers'),
    }
    facets = {
        'reuses': search.RangeFacet('metrics.reuses'),
        'datasets': search.RangeFacet('metrics.datasets'),
        'followers': search.RangeFacet('metrics.followers'),
        'public_services': search.BoolFacet('public_service'),
    }
    mapping = {
        'properties': {
            'name': {
                'type': 'string',
                'analyzer': search.i18n_analyzer,
                'fields': {
                    'raw': {'type': 'string', 'index': 'not_analyzed'}
                }
            },
            'description': {'type': 'string', 'analyzer': search.i18n_analyzer},
            'url': {'type': 'string'},
            'metrics': search.metrics_mapping(Organization),
            'org_suggest': {
                'type': 'completion',
                'index_analyzer': 'simple',
                'search_analyzer': 'simple',
                'payloads': True,
            },
        }
    }
    boosters = [
        search.BoolBooster('public_service', 1.5),
        search.GaussDecay('metrics.followers', max_followers, decay=0.8),
        search.GaussDecay('metrics.reuses', max_reuses, decay=0.9),
        search.GaussDecay('metrics.datasets', max_datasets, decay=0.9),
    ]

    @classmethod
    def is_indexable(cls, org):
        return org.deleted is None

    @classmethod
    def serialize(cls, organization):
        return {
            'name': organization.name,
            'description': organization.description,
            'url': organization.url,
            'metrics': organization.metrics,
            'org_suggest': {
                'input': cls.completer_tokenize(organization.name),
                'output': organization.name,
                'payload': {
                    'id': str(organization.id),
                    'image_url': organization.image_url,
                    'slug': organization.slug,
                },
            },
            'public_service': organization.public_service or False,  # TODO: extract tis into plugin
        }
