# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.tools.safe_eval import safe_eval
from odoo.exceptions import UserError
from odoo.tools.float_utils import float_compare, float_is_zero, float_round

class notas_credito(models.Model):
    _name = 'motivo.nota'

    name=fields.Char(string='Nombre')

class AccountInvoiceRefund(models.TransientModel):
    _inherit ='account.invoice.refund'

    @api.model
    def _get_reason(self):
        context = dict(self._context or {})
        active_id = context.get('active_id', False)
        if active_id:
            inv = self.env['account.invoice'].browse(active_id)
            return inv.name
        return ''    
   
    notas_motivo = fields.Many2one('motivo.nota', string='Motivo')

   	
    description = fields.Char(string='Reason', default="t")

    @api.multi
    def compute_refund(self, mode='refund'):
        inv_obj = self.env['account.invoice']
        inv_tax_obj = self.env['account.invoice.tax']
        inv_line_obj = self.env['account.invoice.line']
        context = dict(self._context or {})
        xml_id = False

        for form in self:
            created_inv = []
            date = False
            description = False
            for inv in inv_obj.browse(context.get('active_ids')):
                if inv.state in ['draft', 'cancel']:
                    raise UserError(_('Cannot create credit note for the draft/cancelled invoice.'))
                if inv.reconciled and mode in ('cancel', 'modify'):
                    raise UserError(_('Cannot create a credit note for the invoice which is already reconciled, invoice should be unreconciled first, then only you can add credit note for this invoice.'))

                date = form.date or False
                description = form.notas_motivo.name or inv.name
                refund = inv.refund(form.date_invoice, date, description, inv.journal_id.id)

                created_inv.append(refund.id)
                if mode in ('cancel', 'modify'):
                    movelines = inv.move_id.line_ids
                    to_reconcile_ids = {}
                    to_reconcile_lines = self.env['account.move.line']
                    for line in movelines:
                        if line.account_id.id == inv.account_id.id:
                            to_reconcile_lines += line
                            to_reconcile_ids.setdefault(line.account_id.id, []).append(line.id)
                        if line.reconciled:
                            line.remove_move_reconcile()
                    refund.action_invoice_open()
                    for tmpline in refund.move_id.line_ids:
                        if tmpline.account_id.id == inv.account_id.id:
                            to_reconcile_lines += tmpline
                    to_reconcile_lines.filtered(lambda l: l.reconciled == False).reconcile()
                    if mode == 'modify':
                        invoice = inv.read(inv_obj._get_refund_modify_read_fields())
                        invoice = invoice[0]
                        del invoice['id']
                        invoice_lines = inv_line_obj.browse(invoice['invoice_line_ids'])
                        invoice_lines = inv_obj.with_context(mode='modify')._refund_cleanup_lines(invoice_lines)
                        tax_lines = inv_tax_obj.browse(invoice['tax_line_ids'])
                        tax_lines = inv_obj._refund_cleanup_lines(tax_lines)
                        invoice.update({
                            'type': inv.type,
                            'date_invoice': form.date_invoice,
                            'state': 'draft',
                            'number': False,
                            'invoice_line_ids': invoice_lines,
                            'tax_line_ids': tax_lines,
                            'date': date,
                            'origin': inv.origin,
                            'fiscal_position_id': inv.fiscal_position_id.id,
                        })
                        for field in inv_obj._get_refund_common_fields():
                            if inv_obj._fields[field].type == 'many2one':
                                invoice[field] = invoice[field] and invoice[field][0]
                            else:
                                invoice[field] = invoice[field] or False
                        inv_refund = inv_obj.create(invoice)
                        body = _('Correction of <a href=# data-oe-model=account.invoice data-oe-id=%d>%s</a><br>Reason: %s') % (inv.id, inv.number, description)
                        inv_refund.message_post(body=body)
                        if inv_refund.payment_term_id.id:
                            inv_refund._onchange_payment_term_date_invoice()
                        created_inv.append(inv_refund.id)
                xml_id = inv.type == 'out_invoice' and 'action_invoice_out_refund' or \
                         inv.type == 'out_refund' and 'action_invoice_tree1' or \
                         inv.type == 'in_invoice' and 'action_invoice_in_refund' or \
                         inv.type == 'in_refund' and 'action_invoice_tree2'
        if xml_id:
            result = self.env.ref('account.%s' % (xml_id)).read()[0]
            if mode == 'modify':
                # When refund method is `modify` then it will directly open the new draft bill/invoice in form view
                if inv_refund.type == 'in_invoice':
                    view_ref = self.env.ref('account.invoice_supplier_form')
                else:
                    view_ref = self.env.ref('account.invoice_form')
                result['views'] = [(view_ref.id, 'form')]
                result['res_id'] = inv_refund.id
            else:
                invoice_domain = safe_eval(result['domain'])
                invoice_domain.append(('id', 'in', created_inv))
                result['domain'] = invoice_domain
            return result
        return True

    @api.multi
    def invoice_refund(self):
        data_refund = self.read(['filter_refund'])[0]['filter_refund']
        return self.compute_refund(data_refund)


class ReturnPicking(models.TransientModel):
    _inherit ='stock.return.picking'

    notas_motivo = fields.Many2one('motivo.nota', string='Motivo')


    def _prepare_move_default_values(self, return_line, new_picking):
        vals = {
            'product_id': return_line.product_id.id,
            'product_uom_qty': return_line.quantity,
            'product_uom': return_line.product_id.uom_id.id,
            'picking_id': new_picking.id,
            'state': 'draft',
            'date_expected': fields.Datetime.now(),
            'location_id': return_line.move_id.location_dest_id.id,
            'location_dest_id': self.location_id.id or return_line.move_id.location_id.id,
            'picking_type_id': new_picking.picking_type_id.id,
            'warehouse_id': self.picking_id.picking_type_id.warehouse_id.id,
            'origin_returned_move_id': return_line.move_id.id,
            'procure_method': 'make_to_stock',
        }
        return vals

    def _create_returns(self):
        # TODO sle: the unreserve of the next moves could be less brutal
        for return_move in self.product_return_moves.mapped('move_id'):
            return_move.move_dest_ids.filtered(lambda m: m.state not in ('done', 'cancel'))._do_unreserve()

        # create new picking for returned products
        picking_type_id = self.picking_id.picking_type_id.return_picking_type_id.id or self.picking_id.picking_type_id.id
        new_picking = self.picking_id.copy({
            'move_lines': [],
            'picking_type_id': picking_type_id,
            'state': 'draft',
            'origin': _("Return of %s") % self.picking_id.name,
            'location_id': self.picking_id.location_dest_id.id,
            'location_dest_id': self.location_id.id,
            'retorno':True})
        new_picking.message_post_with_view('mail.message_origin_link',
            values={'self': new_picking, 'origin': self.picking_id},
            subtype_id=self.env.ref('mail.mt_note').id)
        returned_lines = 0
        for return_line in self.product_return_moves:
            if not return_line.move_id:
                raise UserError(_("You have manually created product lines, please delete them to proceed."))
            # TODO sle: float_is_zero?
            if return_line.quantity:
                returned_lines += 1
                vals = self._prepare_move_default_values(return_line, new_picking)
                r = return_line.move_id.copy(vals)
                vals = {}

                # +--------------------------------------------------------------------------------------------------------+
                # |       picking_pick     <--Move Orig--    picking_pack     --Move Dest-->   picking_ship
                # |              | returned_move_ids              ↑                                  | returned_move_ids
                # |              ↓                                | return_line.move_id              ↓
                # |       return pick(Add as dest)          return toLink                    return ship(Add as orig)
                # +--------------------------------------------------------------------------------------------------------+
                move_orig_to_link = return_line.move_id.move_dest_ids.mapped('returned_move_ids')
                move_dest_to_link = return_line.move_id.move_orig_ids.mapped('returned_move_ids')
                vals['move_orig_ids'] = [(4, m.id) for m in move_orig_to_link | return_line.move_id]
                vals['move_dest_ids'] = [(4, m.id) for m in move_dest_to_link]
                r.write(vals)
        if not returned_lines:
            raise UserError(_("Please specify at least one non-zero quantity."))

        new_picking.action_confirm()
        new_picking.action_assign()
        return new_picking.id, picking_type_id

    def create_returns(self):
        for wizard in self:
            new_picking_id, pick_type_id = wizard._create_returns()
           
        # Override the context to disable all the potential filters that could have been set previously
        ctx = dict(self.env.context)
        ctx.update({
            'search_default_picking_type_id': pick_type_id,
            'search_default_draft': False,
            'search_default_assigned': False,
            'search_default_confirmed': False,
            'search_default_ready': False,
            'search_default_late': False,
            'search_default_available': False,
        })
        return {
            'name': _('Returned Picking'),
            'view_type': 'form',
            'view_mode': 'form,tree,calendar',
            'res_model': 'stock.picking',
            'res_id': new_picking_id,
            'type': 'ir.actions.act_window',
            'context': ctx,
        }


class stock_inherit(models.Model):

    _inherit = 'stock.picking'

    retorno=fields.Boolean(string='retorno')

    @api.multi
    def button_validate(self):
        self.ensure_one()
        if not self.move_lines and not self.move_line_ids:
            raise UserError(_('Please add some items to move.'))

        if self.retorno==True:
            entrada_stock_picking =self.env['stock.picking']
            mov= entrada_stock_picking.search([('origin', '=', self.group_id.name)])
            nombre=mov.id
            entrada_stock_picking_return =self.env['stock.return.picking']
            nota_li= entrada_stock_picking_return.search([('picking_id', '=', nombre)], limit=1)
            nota_id=nota_li.notas_motivo.id
            
            entrada =self.env['sale.order']
            entrada_stock =self.env['stock.move']
            entrada_fac =self.env['account.invoice']
            entrada_fac_line =self.env['account.invoice.line']
            fac = entrada_fac.search([('origin', '=', self.group_id.name)])
            fac_line = entrada_fac_line.search([('origin', '=', self.group_id.name)], limit=1)
            account_id=fac_line.account_id.id
            refund=fac.id
            reference=fac.reference
            origin=fac.number
            partner_shipping_id=fac.partner_shipping_id.id
            date_invoice=fac.date_invoice
            date_due=fac.date_due

            entrada_tax =self.env['account.invoice.tax']
            fac_tax=entrada_tax.search([('invoice_id', '=', refund)], limit=1)
            tax_id=fac_tax.tax_id.id
            lines = entrada.search([('name', '=', self.group_id.name)])

            lines_stock = entrada_stock.search([('reference', '=', self.name)])
            ac_ref=self.env['account.invoice.refund'].create({'filter_refund':'refund','description':'tt','notas_motivo':nota_id,'date_invoice':fields.Datetime.now()})
            if ac_ref:
                name=ac_ref.notas_motivo.name
                if lines:
                    for l in lines:
                        do=self.env['account.invoice'].create({
                            'name':name,
                            'origin':origin,
                            'partner_id':self.partner_id.id,
                            'type':'out_refund',
                            'refund_invoice_id':refund,
                            'reference':reference,
                            'partner_shipping_id':partner_shipping_id,
                            'date_invoice':date_invoice,
                            'date_due':date_due,
                            })
                        invoice=do.id
                        if do:

                            for lis in lines_stock:
                                for li in lis.sale_line_id:
                                    if li.product_id==lis.product_id:
                                        taxes = []
                                        valor = 0.0                                                
                                        for t in li.tax_id:
                                            taxes.append(t.id)
                                            valor=t.amount
                                        amount_tax = 0.0
                                        for line in lis.sale_line_id:
                                            amount_tax += ((lis.product_uom_qty*line.price_unit)*valor)/100  

                                        tax=self.env['account.invoice.tax'].create({
                                        'invoice_id':invoice,
                                        'name':'Impuesto 15.00%',
                                        'tax_id':tax_id,
                                        'account_id':account_id,
                                        'amount':amount_tax,
                                        })

                                        # taxes = []
                                        # valor = 0.0                                                
                                        # for t in li.tax_id:
                                        #     taxes.append(t.id)
                                        #     valor=t.amount

                                        invoice_line=self.env['account.invoice.line'].create({
                                        'invoice_id':invoice,
                                        'sequence':li.sequence,
                                        'product_id':li.product_id.id,
                                        'origin':'test',
                                        #'is_rounding_line'
                                        'name':li.name,
                                        'display_type':li.display_type,
                                        'company_id':li.company_id.id,
                                        'account_id':account_id,
                                        #'account_analytic_id'
                                        #'analytic_tag_ids'
                                        'quantity':lis.product_uom_qty,
                                        'uom_id':li.product_uom.id,
                                        'price_unit':li.price_unit,
                                        'discount':li.discount,
                                        'invoice_line_tax_ids':[(6, 0, taxes)],
                                        'price_subtotal':li.price_subtotal,
                                        'price_total':li.price_total,               
                
                                        })
        # If no lots when needed, raise error
        picking_type = self.picking_type_id
        precision_digits = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        no_quantities_done = all(float_is_zero(move_line.qty_done, precision_digits=precision_digits) for move_line in self.move_line_ids)
        no_reserved_quantities = all(float_is_zero(move_line.product_qty, precision_rounding=move_line.product_uom_id.rounding) for move_line in self.move_line_ids)
        if no_reserved_quantities and no_quantities_done:
            raise UserError(_('You cannot validate a transfer if no quantites are reserved nor done. To force the transfer, switch in edit more and encode the done quantities.'))

        if picking_type.use_create_lots or picking_type.use_existing_lots:
            lines_to_check = self.move_line_ids
            if not no_quantities_done:
                lines_to_check = lines_to_check.filtered(
                    lambda line: float_compare(line.qty_done, 0,
                                               precision_rounding=line.product_uom_id.rounding)
                )

            for line in lines_to_check:
                product = line.product_id
                if product and product.tracking != 'none':
                    if not line.lot_name and not line.lot_id:
                        raise UserError(_('You need to supply a Lot/Serial number for product %s.') % product.display_name)

        if no_quantities_done:
            view = self.env.ref('stock.view_immediate_transfer')
            wiz = self.env['stock.immediate.transfer'].create({'pick_ids': [(4, self.id)]})

           
            return {
                'name': _('Immediate Transfer?'),
                'type': 'ir.actions.act_window',
                'view_type': 'form',
                'view_mode': 'form',
                'res_model': 'stock.immediate.transfer',
                'views': [(view.id, 'form')],
                'view_id': view.id,
                'target': 'new',
                'res_id': wiz.id,
                'context': self.env.context,
            }

        if self._get_overprocessed_stock_moves() and not self._context.get('skip_overprocessed_check'):
            view = self.env.ref('stock.view_overprocessed_transfer')
            wiz = self.env['stock.overprocessed.transfer'].create({'picking_id': self.id})
            return {
                'type': 'ir.actions.act_window',
                'view_type': 'form',
                'view_mode': 'form',
                'res_model': 'stock.overprocessed.transfer',
                'views': [(view.id, 'form')],
                'view_id': view.id,
                'target': 'new',
                'res_id': wiz.id,
                'context': self.env.context,
            }

        # Check backorder should check for other barcodes
        if self._check_backorder():
            return self.action_generate_backorder_wizard()
        self.action_done()
        return

    def action_generate_backorder_wizard(self):
        view = self.env.ref('stock.view_backorder_confirmation')
        wiz = self.env['stock.backorder.confirmation'].create({'pick_ids': [(4, p.id) for p in self]})
        return {
            'name': _('Create Backorder?'),
            'type': 'ir.actions.act_window',
            'view_type': 'form',
            'view_mode': 'form',
            'res_model': 'stock.backorder.confirmation',
            'views': [(view.id, 'form')],
            'view_id': view.id,
            'target': 'new',
            'res_id': wiz.id,
            'context': self.env.context,
        }

  