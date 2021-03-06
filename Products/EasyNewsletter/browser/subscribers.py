# -*- coding: utf-8 -*-
from Acquisition import aq_inner
from logging import getLogger
from plone.i18n.normalizer.interfaces import IIDNormalizer
from Products.CMFCore.utils import getToolByName
from Products.EasyNewsletter import EasyNewsletterMessageFactory as _
from Products.EasyNewsletter.config import SALUTATION
from Products.EasyNewsletter.interfaces import ISubscriberSource
from Products.Five import BrowserView
from Products.statusmessages.interfaces import IStatusMessage
from zope.component import getUtility
from zope.component.interfaces import ComponentLookupError
from zope.interface import implementer
from zope.interface import Interface

import codecs
import cStringIO
import csv
import tempfile


logger = getLogger('Subscribers')


class UTF8Recoder:
    """
    Iterator that reads an encoded stream and reencodes the input to UTF-8
    """
    def __init__(self, f, encoding):
        self.reader = codecs.getreader(encoding)(f)

    def __iter__(self):
        return self

    def next(self):
        return self.reader.next().encode("utf-8")


class UnicodeReader:
    """
    A CSV reader which will iterate over lines in the CSV file "f",
    which is encoded in the given encoding.
    """

    def __init__(self, f, dialect=csv.excel, encoding="utf-8", **kwds):
        f = UTF8Recoder(f, encoding)
        self.reader = csv.reader(f, dialect=dialect, **kwds)

    def next(self):
        row = self.reader.next()
        return [unicode(s, "utf-8") for s in row]

    def __iter__(self):
        return self


class UnicodeWriter:
    """
    A CSV writer which will write rows to CSV file "f",
    which is encoded in the given encoding.
    """

    def __init__(self, f, dialect=csv.excel, encoding="utf-8", **kwds):
        # Redirect output to a queue
        self.queue = cStringIO.StringIO()
        self.writer = csv.writer(self.queue, dialect=dialect, **kwds)
        self.stream = f
        self.encoder = codecs.getincrementalencoder(encoding)()

    def writerow(self, row):
        self.writer.writerow([s.encode("utf-8") for s in row])
        # Fetch UTF-8 output from the queue ...
        data = self.queue.getvalue()
        data = data.decode("utf-8")
        # ... and reencode it into the target encoding
        data = self.encoder.encode(data)
        # write to the target stream
        self.stream.write(data)
        # empty queue
        self.queue.truncate(0)

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)


def normalize_id(astring):
    return getUtility(IIDNormalizer).normalize(astring)


CSV_HEADER = [
    u'salutation',
    u'name_prefix',
    u'firstname',
    u'lastname',
    u'nl_language',
    u'email',
    u'organization'
]


class IEnl_Subscribers_View(Interface):
    """
    Enl_Subscribers_View interface
    """


@implementer(IEnl_Subscribers_View)
class Enl_Subscribers_View(BrowserView):
    """
    Enl_Subscribers_View browser view
    """

    # TODO: we should move these indexes from FieldIndex to ZCTextIndex
    # see setuphandlers.py for indexes creation
    searchable_params = (
        'SearchableText',
    )

    def __init__(self, context, request):
        self.context = context
        self.request = request

    def __call__(self):
        if self.can_delete():
            self.delete()
        return super(Enl_Subscribers_View, self).__call__()

    @property
    def portal_catalog(self):
        return getToolByName(self.context, 'portal_catalog')

    @property
    def portal(self):
        return getToolByName(self.context, 'portal_url').getPortalObject()

    @property
    def query(self):
        query = dict(
            portal_type='ENLSubscriber',
            path='/'.join(self.context.getPhysicalPath()),
            sort_on='email'
        )
        form = self.request.form
        for k in self.searchable_params:
            if form.get(k):
                if k == 'SearchableText':
                    searchterm = form.get(k)
                    if not searchterm.endswith('*'):
                        searchterm += '*'
                    query[k] = searchterm
                else:
                    query[k] = form.get(k)
        return query

    def subscribers(self):
        subscribers = list()

        # Plone subscribers
        for brain in self.portal_catalog(self.query):
            if brain.salutation:
                salutation = SALUTATION.getValue(brain.salutation, '')
            else:
                salutation = ''
            subscribers.append(dict(
                id=brain.getId,
                source='plone',
                deletable=True,
                email=brain.email,
                getURL=brain.getURL(),
                salutation=salutation,
                name_prefix=brain.name_prefix,
                firstname=brain.firstname,
                lastname=brain.lastname,
                nl_language=brain.nl_language,
                organization=brain.organization
            ))

        # External subscribers
        external_source_name = self.context.getSubscriberSource()
        if external_source_name != 'default':
            try:
                external_source = getUtility(
                    ISubscriberSource, name=external_source_name)
            except ComponentLookupError:
                pass

            for subscriber in external_source.getSubscribers(self.context):
                subscriber['source'] = external_source_name
                subscribers.append(subscriber)

        return subscribers

    def can_delete(self):
        meth = self.request.get('REQUEST_METHOD')
        delete_button = self.request.get('delete')
        return meth.lower() == 'post' and delete_button

    def delete(self):
        """ delete all the selected subscribers
        """
        msg_manager = IStatusMessage(self.request)
        ids = self.request.get('subscriber_ids', [])
        if not ids:
            msg = _(u"No subscriber selected!")
            msg_manager.addStatusMessage(msg, type='error')
            return False
        existing = self.context.objectIds()
        # avoid wrong id to be submitted
        to_remove = [i for i in ids if i in existing]
        self.context.manage_delObjects(to_remove)
        msg = _(u"subscriber/s deleted successfully")
        msg_manager.addStatusMessage(msg, type="info")
        return True


class UploadCSV(BrowserView):

    def __init__(self, context, request):
        self.context = context
        self.request = request

    def create_subscribers(self, csv_data=None):
        """Create newsletter subscribers from uploaded CSV file.
        """

        # Do nothing if no submit button was hit
        if 'form.button.Import' not in self.request.form:
            return

        context = aq_inner(self.context)
        lang = context.Language()
        # plone_utils = getToolByName(self.context, 'plone_utils')
        # encoding = plone_utils.getSiteEncoding()
        existing = self.context.objectIds()
        # messages = IStatusMessage(self.request)
        success = []
        fail = []

        # Show error if no file was specified
        filename = self.request.form.get('csv_upload', None)
        if not filename:
            msg = _('No file specified.')
            IStatusMessage(self.request).addStatusMessage(msg, type='error')
            return self.request.response.redirect(
                context.absolute_url() + '/@@upload_csv')

        # Show error if no data has been provided in the file
        reader = UnicodeReader(filename)
        header = reader.next()
        if header != [x for x in CSV_HEADER]:
            logger.info("Got header %s\n Expected:%s" % (
                header, CSV_HEADER))
            msg = _(
                'Wrong specification of the CSV file. ' +
                'Please correct it and retry.')
            IStatusMessage(self.request).addStatusMessage(msg, type='error')
            return self.request.response.redirect(
                context.absolute_url() + '/@@upload_csv')

        for subscriber in reader:
            # Check the length of the line
            if len(subscriber) != 7:
                msg = _('The number of items in the line is not correct. \
                        It should be 7. Check your CSV file.')
                fail.append(
                    {'failure': msg})
            else:
                salutation = subscriber[0]
                name_prefix = subscriber[1]
                firstname = subscriber[2]
                lastname = subscriber[3]
                nl_language = subscriber[4]
                email = subscriber[5]
                organization = subscriber[6]
                id = normalize_id(email)
                if id in existing:
                    msg = _('This email address is already registered.')
                    fail.append(
                        {'salutation': salutation,
                         'name_prefix': name_prefix,
                         'firstname': firstname,
                         'lastname': lastname,
                         'nl_language': nl_language,
                         'email': email,
                         'organization': organization,
                         'failure': msg})
                else:
                    title = email + " - " + ' '.join([lastname,
                                                      firstname])
                    try:
                        self.context.invokeFactory(
                            'ENLSubscriber',
                            id=id,
                            title=title,
                            description="",
                            language=lang)
                        sub = context[id]
                        sub.email = email
                        sub.name_prefix = name_prefix
                        sub.firstname = firstname
                        sub.lastname = lastname
                        sub.nl_language = nl_language
                        sub.organization = organization
                        sub.salutation = salutation
                        obj = self.context.get(id, None)
                        obj.reindexObject()
                        # update existing
                        existing.append(id)
                        success.append({
                            'salutation': salutation,
                            'name_prefix': name_prefix,
                            'firstname': firstname,
                            'lastname': lastname,
                            'nl_language': nl_language,
                            'email': email,
                            'organization': organization
                        })
                    except Exception, e:
                        fail.append({
                            'salutation': salutation,
                            'name_prefix': name_prefix,
                            'firstname': firstname,
                            'lastname': lastname,
                            'nl_language': nl_language,
                            'email': email,
                            'organization': organization,
                            'failure': (
                                'An error occured while creating ' +
                                'this subscriber: %s') % str(e)})

        return {'success': success, 'fail': fail}


class DownloadCSV(BrowserView):

    def __call__(self):
        """Returns a CSV file with all newsletter subscribers.
        """
        context = aq_inner(self.context)
        ctool = getToolByName(context, 'portal_catalog')

        # Create CSV file
        filename = tempfile.mktemp()
        file = open(filename, 'wb')
        csvWriter = UnicodeWriter(file,
                                  {'delimiter': ',',
                                   'quotechar': '"',
                                   'quoting': csv.QUOTE_MINIMAL})
        csvWriter.writerow([x for x in CSV_HEADER])
        for subscriber in ctool(portal_type='ENLSubscriber',
                                path='/'.join(self.context.getPhysicalPath()),
                                sort_on='email'):
            obj = subscriber.getObject()
            csvWriter.writerow([obj.salutation,
                                obj.name_prefix,
                                obj.firstname,
                                obj.lastname,
                                obj.nl_language,
                                obj.email,
                                obj.organization])
        file.close()
        data = open(filename, "r").read()

        # Create response
        response = context.REQUEST.response
        response.addHeader(
            'Content-Disposition',
            "attachment; filename=easynewsletter-subscribers.csv")
        response.addHeader('Content-Type', 'text/csv')
        response.addHeader('Content-Length', "%d" % len(data))
        response.addHeader('Pragma', "no-cache")
        response.addHeader(
            'Cache-Control',
            "must-revalidate, post-check=0, pre-check=0, public")
        response.addHeader('Expires', "0")

        # Return CSV data
        return data
