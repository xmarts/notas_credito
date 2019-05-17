# -*- coding: utf-8 -*-
from odoo import http

# class NotasCredito(http.Controller):
#     @http.route('/notas_credito/notas_credito/', auth='public')
#     def index(self, **kw):
#         return "Hello, world"

#     @http.route('/notas_credito/notas_credito/objects/', auth='public')
#     def list(self, **kw):
#         return http.request.render('notas_credito.listing', {
#             'root': '/notas_credito/notas_credito',
#             'objects': http.request.env['notas_credito.notas_credito'].search([]),
#         })

#     @http.route('/notas_credito/notas_credito/objects/<model("notas_credito.notas_credito"):obj>/', auth='public')
#     def object(self, obj, **kw):
#         return http.request.render('notas_credito.object', {
#             'object': obj
#         })