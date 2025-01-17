import datetime

from ckan.lib.helpers import json, date_str_to_datetime
import ckan.plugins.toolkit as t
import ckanext.report.helpers as helpers
from ckanext.report.report_registry import Report
from ckan.lib.render import TemplateNotFound
from ckan.common import OrderedDict, _

log = __import__('logging').getLogger(__name__)

c = t.c


class ReportController(t.BaseController):

    def index(self):
        try:
            reports = t.get_action('report_list')({}, {})
        except t.NotAuthorized:
            t.abort(401)

        return t.render('report/index.html', extra_vars={'reports': reports})

    def view(self, report_name, organization=None, refresh=False):
        try:
            report = t.get_action('report_show')({}, {'id': report_name})
        except t.NotAuthorized:
            t.abort(401)
        except t.ObjectNotFound:
            t.abort(404)

        # ensure correct url is being used
        if 'organization' in t.request.environ['pylons.routes_dict'] and \
            'organization' not in report['option_defaults']:
                t.redirect_to(helpers.relative_url_for(organization=None))
        elif 'organization' not in t.request.environ['pylons.routes_dict'] and\
            'organization' in report['option_defaults'] and \
            report['option_defaults']['organization']:
                org = report['option_defaults']['organization']
                t.redirect_to(helpers.relative_url_for(organization=org))
        if 'organization' in t.request.params:
            # organization should only be in the url - let the param overwrite
            # the url.
            t.redirect_to(helpers.relative_url_for())

        # options
        options = Report.add_defaults_to_options(t.request.params, report['option_defaults'])
        option_display_params = {}
        if 'format' in options:
            format = options.pop('format')
        else:
            format = None
        if 'organization' in report['option_defaults']:
            options['organization'] = organization
        options_html = {}
        c.options = options  # for legacy genshi snippets

        start_date = None
        end_date = None
        filter_date = None

        if 'filter_for_date' in options:
            filter_date = options.get('filter_for_date')

        if 'start_date' in options:
            start_date = options.pop('start_date')

        if 'end_date' in options:
            end_date = options.pop('end_date')

        for option in options:
            if option not in report['option_defaults']:
                # e.g. 'refresh' param
                log.warn('Not displaying report option HTML for param %s as option not recognized')
                continue
            option_display_params = {'value': options[option],
                                     'default': report['option_defaults'][option],
                                     'extras': {}
                                     }
            try:
                if 'filter_for_date' == option:
                    option_display_params['extras'] = {'start_date': start_date, 'end_date': end_date}

                options_html[option] = \
                    t.render_snippet('report/option_%s.html' % option,
                                     data=option_display_params)
            except TemplateNotFound:
                log.warn('Not displaying report option HTML for param %s as no template found')
                continue


        # Alternative way to refresh the cache - not in the UI, but is
        # handy for testing
        try:
            refresh = t.asbool(t.request.params.get('refresh'))
            if 'refresh' in options:
                options.pop('refresh')
        except ValueError:
            refresh = False

        # Refresh the cache if requested
        if t.request.method == 'POST' and not format:
            refresh = True

        if refresh:
            try:
               t.get_action('report_refresh')({}, {'id': report_name, 'options': options})
            except t.NotAuthorized:
               t.abort(401)
            # Don't want the refresh=1 in the url once it is done
            t.redirect_to(helpers.relative_url_for(refresh=None))

        # Check for any options not allowed by the report
        for key in options:
            if key not in report['option_defaults']:
                t.abort(400, 'Option not allowed by report: %s' % key)

        try:
            data, report_date = t.get_action('report_data_get')({}, {'id': report_name, 'options': options})
        except t.ObjectNotFound:
            t.abort(404)
        except t.NotAuthorized:
            t.abort(401)

        # Nexura - aorejuela
        # Filter for date.
        filtered_data = []
        for item in data['table']:

            try:
                dt_creation_at = date_str_to_datetime(item['creation_at']).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0
                )
                dt_update_at = date_str_to_datetime(item['update_at']).replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0
                )
            except KeyError:
                filtered_data = data['table']
                break

            dt_start_date = None
            dt_end_date = None

            if start_date:
                dt_start_date = date_str_to_datetime(start_date)

            if end_date:
                dt_end_date = date_str_to_datetime(end_date)

            if filter_date and dt_start_date and dt_end_date:
                if start_date <= end_date:
                    if filter_date == 'created' and dt_start_date <= dt_creation_at <= dt_end_date:
                        filtered_data.append(item)
                    elif filter_date == 'updated' and dt_start_date <= dt_update_at <= dt_end_date:
                        filtered_data.append(item)
            elif filter_date and dt_start_date and not dt_end_date:
                if filter_date == 'created' and dt_start_date <= dt_creation_at:
                    filtered_data.append(item)
                elif filter_date == 'updated' and dt_start_date <= dt_update_at:
                    filtered_data.append(item)
            elif filter_date and dt_end_date and not dt_start_date:
                if filter_date == 'created' and dt_creation_at <= dt_end_date:
                    filtered_data.append(item)
                elif filter_date == 'updated' and dt_update_at <= dt_end_date:
                    filtered_data.append(item)
            else:
                filtered_data.append(item)

        # This functionality couples the Datic extension with Report extension.
        if report_name == 'global-statistics' and 'show_global_totals_by' in options:
            show_global_totals_by = options['show_global_totals_by']
            if show_global_totals_by == 'organisms':
                filtered_data = data_and_counting_by_organization(filtered_data)
            elif show_global_totals_by == 'users':
                filtered_data = data_and_counting_by_user(filtered_data)

        data['table'] = filtered_data

        if format and format != 'html':
            ensure_data_is_dicts(data)
            anonymise_user_names(data, organization=options.get('organization'))
            if format == 'csv':
                try:
                    key = t.get_action('report_key_get')({}, {'id': report_name, 'options': options})
                except t.NotAuthorized:
                    t.abort(401)
                filename = 'report_%s.csv' % key
                t.response.headers['Content-Type'] = 'application/csv'
                t.response.headers['Content-Disposition'] = str('attachment; filename=%s' % (filename))
                return make_csv_from_dicts(data['table'])
            elif format == 'json':
                t.response.headers['Content-Type'] = 'application/json'
                data['generated_at'] = report_date
                return json.dumps(data)
            else:
                t.abort(400, 'Format not known - try html, json or csv')

        are_some_results = bool(data['table'] if 'table' in data
                                else data)

        # A couple of context variables for legacy genshi reports
        c.data = data
        c.options = options
        return t.render('report/view.html', extra_vars={
            'report': report, 'report_name': report_name, 'data': data,
            'report_date': report_date, 'options': options,
            'options_html': options_html,
            'report_template': report['template'],
            'are_some_results': are_some_results,
            'start_date': start_date,
            'end_date': end_date})


def make_csv_from_dicts(rows):
    import csv
    import cStringIO as StringIO

    csvout = StringIO.StringIO()
    csvwriter = csv.writer(
        csvout,
        dialect='excel',
        quoting=csv.QUOTE_NONNUMERIC
    )
    # extract the headers by looking at all the rows and
    # get a full list of the keys, retaining their ordering
    headers_ordered = []
    headers_set = set()
    for row in rows:
        new_headers = set(row.keys()) - headers_set
        headers_set |= new_headers
        for header in row.keys():
            if header in new_headers:
                headers_ordered.append(header)
    translated_headers = translate_headers(headers_ordered)
    csvwriter.writerow(translated_headers)
    for row in rows:
        items = []
        for header in headers_ordered:
            item = row.get(header, 'no record')
            if isinstance(item, datetime.datetime):
                item = item.strftime('%Y-%m-%d %H:%M')
            elif isinstance(item, (int, long, float, list, tuple)):
                item = unicode(item)
            elif item is None:
                item = ''
            else:
                item = item.encode('utf8')
            items.append(item)
        try:
            csvwriter.writerow(items)
        except Exception, e:
            raise Exception("%s: %s, %s" % (e, row, items))
    csvout.seek(0)
    return csvout.read()


def ensure_data_is_dicts(data):
    '''Ensure that the data is a list of dicts, rather than a list of tuples
    with column names, as sometimes is the case. Changes it in place'''
    if data['table'] and isinstance(data['table'][0], (list, tuple)):
        new_data = []
        columns = data['columns']
        for row in data['table']:
            new_data.append(OrderedDict(zip(columns, row)))
        data['table'] = new_data
        del data['columns']


def anonymise_user_names(data, organization=None):
    '''Ensure any columns with names in are anonymised, unless the current user
    has privileges.

    NB this is only enabled for data.gov.uk - it is custom functionality.
    '''
    try:
        import ckanext.dgu.lib.helpers as dguhelpers
    except ImportError:
        # If this is not DGU then cannot do the anonymization
        return
    column_names = data['table'][0].keys() if data['table'] else []
    for col in column_names:
        if col.lower() in ('user', 'username', 'user name', 'author'):
            for row in data['table']:
                row[col] = dguhelpers.user_link_info(
                    row[col], organization=organization)[0]


def calculate_global_totals_of_downloads_and_views(packages):
    visits = 0
    downloads = 0

    for package in packages:
        visits += package['visits']
        downloads += package['downloads']

    return downloads, visits


def data_and_counting_by_organization(filtered_data):
    organization_list = t.get_action('organization_list')(data_dict={'all_fields': True})
    table = []

    for organization in organization_list:
        packages = []
        for item in filtered_data:
            if item['organism_name'] == organization['name']:
                packages.append(item)

        downloads, visits = calculate_global_totals_of_downloads_and_views(packages)

        data = OrderedDict((
            ('show_global_totals_by', organization['display_name']),
            ('package_count', len(packages)),
            ('downloads', downloads),
            ('visits', visits),
        ))

        table.append(data)

    return table


def data_and_counting_by_user(filtered_data):
    user_list = t.get_action('user_list')(data_dict={})
    table = []

    for user in user_list:
        packages = []
        for item in filtered_data:
            if item['user_name'] == user['name']:
                packages.append(item)

        downloads, visits = calculate_global_totals_of_downloads_and_views(packages)

        data = OrderedDict((
            ('show_global_totals_by', user['display_name']),
            ('package_count', len(packages)),
            ('downloads', downloads),
            ('visits', visits),
        ))

        table.append(data)

    return table


def translate_headers(headers):
    """
    Translates the texts of the headings into legible texts in Spanish.
    :param headers:
    :return: translated headers
    """
    translated_headers = []
    for header in headers:
        translated = _(header)
        item = translated.encode('utf8')
        translated_headers.append(item)

    return translated_headers
