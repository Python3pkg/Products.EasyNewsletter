# -*- coding: utf-8 -*-
from logging import getLogger
from nameparser import HumanName
from plone import api
from plone.app.upgrade.utils import loadMigrationProfile
from Products.Archetypes.interfaces import IReferenceable
from Products.CMFCore.utils import getToolByName


logger = getLogger('Products.EasyNewsletter')


def reinstall_gs_profile(context):
    loadMigrationProfile(
        context,
        'profile-Products.EasyNewsletter:default'
    )
    loadMigrationProfile(
        context,
        'profile-Products.EasyNewsletter:install-base'
    )
    logger.info("Products.EasyNewsletter generic setup profile re-installed")


def fullname_to_first_and_lastname(context):
    """Migrate subscriber fullname to separate fields."""

    catalog = api.portal.get_tool("portal_catalog")
    subscribers = catalog(portal_type='ENLSubscriber')

    for subscriber in subscribers:
        obj = subscriber.getObject()
        name = ''
        try:
            name = HumanName(obj.fullname)
        except Exception:
            logger.info(
                'No splitting necessary for {0}'.format(obj.getTitle()))
        if name:
            if not obj.getLastname():
                obj.setLastname(name.last)
            if not obj.getFirstname():
                obj.setFirstname(name.first)
            if not obj.getName_prefix():
                obj.setName_prefix(name.title)
            obj.reindexObject()
            logger.info(
                'Splitting fullname to first and lastname for {0}'.format(
                    obj.getTitle()
                )
            )

    loadMigrationProfile(
        context,
        'profile-Products.EasyNewsletter:default'
    )


def reindex_subscribers(context):
    """Reindex subscribers"""

    catalog = api.portal.get_tool("portal_catalog")
    subscribers = catalog(portal_type='ENLSubscriber')

    for subscriber in subscribers:
        obj = subscriber.getObject()
        obj.reindexObject()


def apply_referenceable_behavior(context):
    # See plone.app.referenceablebehavior.uidcatalog.
    uid_catalog = getToolByName(context, 'uid_catalog')
    portal_catalog = getToolByName(context, 'portal_catalog')
    brains = portal_catalog(
        meta_type=['Dexterity Item', 'Dexterity Container'])
    for brain in brains:
        obj = brain.getObject()
        if IReferenceable.providedBy(obj):
            path = '/'.join(obj.getPhysicalPath())
            logger.info("""Applying referenceable behavior for
                        object at path %s""", path)
            uid_catalog.catalog_object(obj, path)
