# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import copy
import logging

from flask import request
from werkzeug.urls import Href

from elasticsearch_dsl import Search

from udata.models import db
from udata.search import es, i18n_analyzer, DEFAULT_PAGE_SIZE, adapter_catalog
from udata.search.result import SearchIterator


log = logging.getLogger(__name__)


class SearchQuery(object):
    '''
    This wraps an ElasticSearch query
    '''

    def __init__(self, *adapters, **kwargs):
        self.adapter = adapters[0]
        if issubclass(self.adapter, db.Document):
            self.adapter = adapter_catalog[self.adapter]
        self.adapters = adapters
        self.kwargs = kwargs

        try:
            self.page = max(int(self.kwargs.get('page', 1) or 1), 1)
        except:
            self.page = 1
        try:
            self.page_size = int(
                self.kwargs.get('page_size', DEFAULT_PAGE_SIZE) or
                DEFAULT_PAGE_SIZE)
        except:
            self.page_size = DEFAULT_PAGE_SIZE

    def execute(self):
        qs = self.build_query()
        try:
            return qs.execute()
        except:
            log.exception('Unable to execute search query')

    def iter(self):
        try:
            body = self.get_body()
            # Remove aggregations to avoid overhead on large pagination.
            if 'aggregations' in body:
                del body['aggregations']
            result = es.scan(index=es.index_name,
                             doc_type=self.adapter.doc_type(),
                             body=body)
        except:
            log.exception('Unable to execute search query')
            result = None
        return SearchIterator(self, result)

    def build_query(self):
        qs = Search(using=es.client, index=es.index_name)
        # Sorting
        qs = self.get_sort(qs)
        # Pagination
        start = (self.page - 1) * self.page_size
        qs = qs[start:start + self.page_size]
        # Aggregations
        qs = self.build_aggregations(qs)
        # don't return any fields, just the metadata
        qs = qs.fields([])

        if hasattr(self.adapter, 'boosters') and self.adapter.boosters:
            qs.update_from_dict({'query': {
                'function_score': {
                    'query': self.get_query(),
                    'functions': self.get_score_functions(),
                }
            }})
        else:
            qs.update_from_dict({'query': self.get_query()})

        return qs

    def get_score_functions(self):
        return [b.to_query() for b in self.adapter.boosters]

    def get_sort(self, qs):
        '''Build sort query paramter from kwargs'''
        sorts = self.kwargs.get('sort', [])
        sorts = [sorts] if isinstance(sorts, basestring) else sorts
        sorts = [(s[1:], 'desc')
                 if s.startswith('-') else (s, 'asc')
                 for s in sorts]
        return qs.sort(*[
            {self.adapter.sorts[s].field: d}
            for s, d in sorts if s in self.adapter.sorts
        ])

    def get_filter(self):
        return {}

    def _bool_query(self):
        return {'must': [], 'must_not': [], 'should': []}

    def initial_bool_query(self):
        return self._bool_query()

    def _update_bool_query(self, query, new):
        if not query:
            query = self._bool_query()
        for key in 'must', 'must_not', 'should':
            query[key].extend(new.get(key, []))
        return query

    def _multi_match(self, terms):
        query = {
            'multi_match': {
                'query': ' '.join(terms),
                'analyzer': self.adapter.analyzer or i18n_analyzer,
                'type': self.adapter.match_type
            }
        }
        if self.adapter.fields:
            query['multi_match']['fields'] = self.adapter.fields
        if self.adapter.fuzzy:
            query['multi_match']['fuzziness'] = 'AUTO'
            query['multi_match']['prefix_length'] = 2  # Make it configurable
        return query

    def build_text_query(self):
        '''Build full text query from kwargs'''
        qs = self.kwargs.get('q', '')
        included, excluded = [], []
        terms = qs.split(' ') if isinstance(qs, basestring) else qs
        for term in terms:
            if not term.strip():
                continue
            if term.startswith('-'):
                excluded.append(term[1:])
            else:
                included.append(term)
        query = self._bool_query()
        if included:
            query['must'].append(self._multi_match(included))
        if excluded:
            query['must_not'].append(self._multi_match(excluded))
        return query

    def build_aggregation_queries(self):
        '''Build aggregation query parameters from kwargs'''
        query = self._bool_query()
        if not self.adapter.facets:
            return query
        for name, aggregation in self.adapter.facets.items():
            new_query = aggregation.filter_from_kwargs(name, self.kwargs)
            if not new_query:
                continue
            self._update_bool_query(query, new_query)
        return query

    def build_aggregations(self, qs):
        for name in self.facets_kwargs:
            aggregation = self.adapter.facets[name]
            args = self.kwargs.get(name, [])
            aggs = aggregation.to_aggregations(name, *args)
            if aggs:
                for name, agg in aggs.items():
                    qs.aggs.bucket(name, agg)
        return qs

    @property
    def facets_kwargs(self):
        '''List expected facets from kwargs'''
        facets = self.kwargs.get('facets')
        if not self.adapter.facets or not facets:
            return []
        if isinstance(facets, basestring):
            facets = [facets]
        if facets is True or 'all' in facets:
            return self.adapter.facets.keys()
        else:
            return [
                f for f in self.adapter.facets.keys()
                if f in facets
            ]

    def get_query(self):
        query = self.build_text_query()
        self._update_bool_query(query, self.build_aggregation_queries())

        has_query = False
        for key in 'must', 'must_not', 'should':
            if not query[key]:
                del query[key]
            else:
                has_query = True

        return {'bool': query} if has_query else {'match_all': {}}

    def to_url(self, url=None, replace=False, **kwargs):
        '''Serialize the query into an URL'''
        params = copy.deepcopy(self.kwargs)
        if kwargs:
            params.pop('page', None)
            for key, value in kwargs.items():
                if not replace and key in params:
                    if not isinstance(params[key], (list, tuple)):
                        params[key] = [params[key], value]
                    else:
                        params[key].append(value)
                else:
                    params[key] = value
        params.pop('facets', None)  # Always true when used
        href = Href(url or request.base_url)
        return href(params)
